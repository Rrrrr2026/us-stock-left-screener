#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股 Market 适配器 — leftside_core 共用核心的全部市场差异都在这里
==================================================================
回测交易规则 (当日可卖 / 无涨跌停 / 0.2% 往返成本)、成长质量标签、价格序列
(yfinance 批量, 自动复权)、基准指数、个股新闻标题与风险关键词。
"""
from __future__ import annotations
import logging

from .config import DASHBOARD_DIR, DATA_DIR, DB_PATH
from leftside_core.market import Market, set_market

log = logging.getLogger("screener.market")

GROWTH_TIER = {"持续增长": "G", "拐点向上": "G", "增速回落": "M",
               "增长下滑": "W", "单季脉冲": "W"}
TIER_LABEL = {"G": "🟢 持续/拐点", "M": "🟡 增速回落", "W": "🔴 下滑/脉冲", "NA": "⚪ 无数据"}

NEWS_KEYWORDS = [
    ("downgrade", "downgrade"), ("cuts guidance", "guidance cut"), ("lowers guidance", "guidance cut"),
    ("guidance", "guidance"), ("misses", "miss"), ("miss ", "miss"), ("lawsuit", "lawsuit"),
    ("class action", "lawsuit"), ("investigation", "investigation"), ("probe", "investigation"),
    ("sec ", "SEC"), ("offering", "offering"), ("dilut", "dilution"), ("resign", "executive change"),
    ("steps down", "executive change"), ("ceo", "executive change"), ("layoff", "layoffs"),
    ("recall", "recall"), ("fda", "FDA"), ("delist", "delisting"), ("bankrupt", "bankruptcy"),
    ("fraud", "fraud"), ("short seller", "short report"), ("short report", "short report"),
    ("activist", "activist"), ("tariff", "tariffs"), ("warning", "warning"), ("plunge", "selloff"),
    ("tumble", "selloff"), ("sinks", "selloff"),
]


def fetch_price_series(codes: list, start: str) -> dict:
    """code -> {"dates":[...], "ohlc": ndarray[N,4] (o,h,l,c)}; yfinance 自动复权, 100只一批。"""
    res = {}
    try:
        import yfinance as yf
        import pandas as pd
        for i in range(0, len(codes), 100):
            batch = codes[i:i + 100]
            try:
                df = yf.download(batch, start=start, auto_adjust=True,
                                 progress=False, group_by="ticker", threads=True)
            except Exception as e:
                log.warning("yf batch %d 失败: %s", i // 100, e)
                continue
            for c in batch:
                try:
                    sub = df[c] if isinstance(df.columns, pd.MultiIndex) else df
                    sub = sub.dropna(subset=["Open", "High", "Low", "Close"])
                    if len(sub) < 5:
                        continue
                    res[c] = {
                        "dates": [d.strftime("%Y-%m-%d") for d in sub.index],
                        "ohlc": sub[["Open", "High", "Low", "Close"]].to_numpy(float),
                    }
                except Exception:
                    continue
            log.info("价格进度 %d/%d (拿到 %d)", min(i + 100, len(codes)), len(codes), len(res))
    except Exception as e:
        log.warning("yfinance 不可用: %s", e)
    return res


def fetch_benchmark():
    from . import datasource as ds
    return ds.fetch_benchmark()


def news_titles(code: str) -> list:
    import datetime as dt
    try:
        import yfinance as yf
        items = yf.Ticker(code).news or []
        out = []
        for it in items:
            c = it.get("content") or it
            t = str(c.get("title") or "").strip()
            d = str(c.get("pubDate") or c.get("displayTime") or "")[:10]
            if not d and c.get("providerPublishTime"):
                d = dt.datetime.utcfromtimestamp(int(c["providerPublishTime"])).date().isoformat()
            u = ((c.get("canonicalUrl") or {}).get("url") if isinstance(c.get("canonicalUrl"), dict)
                 else c.get("link") or "")
            if t and d:
                out.append((d, t, u or ""))
        return out
    except Exception as e:
        log.debug("news %s failed: %s", code, e)
        return []


def _skip_us_today() -> str | None:
    """美股收盘(≈UTC 20:00/21:00)前丢当日未走完bar; 统一按 UTC 21:10 保守判断。"""
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    if now.hour < 21 or (now.hour == 21 and now.minute < 10):
        return now.date().isoformat()
    return None


def fetch_bars_bulk(codes: list, start: str) -> dict:
    """长历史日线(含成交量), yfinance 批量下载 -> {code: [(d,o,h,l,c,v), ...]}。"""
    import time
    res = {}
    skip_day = _skip_us_today()
    try:
        import pandas as pd
        import yfinance as yf
        for i in range(0, len(codes), 50):
            batch = codes[i:i + 50]
            df = None
            for attempt in (1, 2):
                try:
                    df = yf.download(batch, start=start, auto_adjust=True,
                                     progress=False, group_by="ticker", threads=True)
                    break
                except Exception as e:
                    log.warning("yf 长历史批 %d 第%d次失败: %s", i // 50, attempt, e)
                    time.sleep(5 * attempt)
            if df is None or len(df) == 0:
                continue
            for c in batch:
                try:
                    sub = df[c] if isinstance(df.columns, pd.MultiIndex) else df
                    sub = sub.dropna(subset=["Open", "High", "Low", "Close"])
                    if len(sub) < 60:
                        continue
                    rows = []
                    for d, r in sub.iterrows():
                        d1 = d.strftime("%Y-%m-%d")
                        if skip_day and d1 >= skip_day:
                            continue
                        o, h, l, cl = (float(r["Open"]), float(r["High"]),
                                       float(r["Low"]), float(r["Close"]))
                        v = float(r.get("Volume") or 0)
                        if h < l or min(o, h, l, cl) <= 0:
                            continue
                        rows.append((d1, o, h, l, cl, v))
                    if len(rows) >= 60:
                        res[c] = rows
                except Exception:
                    continue
            log.info("长历史进度 %d/%d (拿到 %d)", min(i + 50, len(codes)), len(codes), len(res))
            time.sleep(1.0)
    except Exception as e:
        log.warning("yfinance 不可用: %s", e)
    return res


def fetch_index_bars(start: str) -> list:
    """SPY 长历史日线 -> [(d,o,h,l,c,v), ...]。"""
    r = fetch_bars_bulk(["SPY"], start)
    return r.get("SPY") or []


def universe_codes() -> list:
    from . import datasource as ds
    uni = ds.get_universe()
    if uni is None or uni.empty:
        return []
    return sorted({str(c) for c in uni["code"] if c})


MARKET = set_market(Market(
    name="us",
    dashboard_dir=DASHBOARD_DIR, data_dir=DATA_DIR, db_path=DB_PATH,
    t_plus_one=False, limit_boards=False, cost_rt=0.002,
    growth_tier=GROWTH_TIER, tier_label=TIER_LABEL,
    fetch_price_series=fetch_price_series, fetch_benchmark=fetch_benchmark,
    limit_up_oneline=None, limit_down_oneline=None,
    news_titles=news_titles, news_keywords=NEWS_KEYWORDS,
    fetch_bars_bulk=fetch_bars_bulk, fetch_index_bars=fetch_index_bars,
    universe_codes=universe_codes,
    log_prefix="screener",
))
