#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据源访问层 (美股, yfinance)
=============================
封装 Yahoo Finance (yfinance):
  * 股票池 + GICS 板块: 标普500 成分 (GitHub CSV / 维基百科 备份 / 内置兜底)
  * 个股日线: yf.Ticker(t).history(period, auto_adjust)
  * 个股基本面/板块/52周: yf.Ticker(t).info
  * 板块指数代理: SPDR 行业 ETF; 基准: SPY
带缓存 + 重试; 单只失败只跳过并记录, 不打断整轮。
"""
from __future__ import annotations
import os
import io
import time
import pickle
import hashlib
import datetime as dt
import logging

import numpy as np
import pandas as pd

from .config import CONFIG, DATA_DIR

log = logging.getLogger("screener.datasource")

_CACHE_DIR = CONFIG["source"]["cache_dir"]
os.makedirs(_CACHE_DIR, exist_ok=True)

_UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")}

# 兜底最小股票池 (CSV/维基都拿不到时, 至少能跑)
_FALLBACK_UNIVERSE = [
    ("AAPL", "Apple", "Information Technology"), ("MSFT", "Microsoft", "Information Technology"),
    ("NVDA", "NVIDIA", "Information Technology"), ("AMZN", "Amazon", "Consumer Discretionary"),
    ("GOOGL", "Alphabet", "Communication Services"), ("META", "Meta Platforms", "Communication Services"),
    ("TSLA", "Tesla", "Consumer Discretionary"), ("JPM", "JPMorgan Chase", "Financials"),
    ("JNJ", "Johnson & Johnson", "Health Care"), ("XOM", "Exxon Mobil", "Energy"),
    ("UNH", "UnitedHealth", "Health Care"), ("V", "Visa", "Financials"),
    ("PG", "Procter & Gamble", "Consumer Staples"), ("HD", "Home Depot", "Consumer Discretionary"),
    ("CAT", "Caterpillar", "Industrials"), ("NEE", "NextEra Energy", "Utilities"),
    ("LIN", "Linde", "Materials"), ("AMT", "American Tower", "Real Estate"),
]


def _yf():
    import yfinance as yf
    return yf


def _yf_symbol(code: str) -> str:
    # yfinance 的多类股用 '-' (如 BRK.B -> BRK-B)
    return str(code).replace(".", "-").strip().upper()


# ===========================================================================
#  缓存
# ===========================================================================
def _cache_key(name: str, *args) -> str:
    raw = name + "|" + "|".join(str(a) for a in args)
    return f"{name}_{hashlib.md5(raw.encode()).hexdigest()[:16]}"


def _cache_load(key):
    if not CONFIG["source"]["use_cache"]:
        return None
    path = os.path.join(_CACHE_DIR, key + ".pkl")
    if not os.path.exists(path):
        return None
    if (time.time() - os.path.getmtime(path)) / 3600.0 > CONFIG["source"]["cache_ttl_hours"]:
        return None
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def _cache_save(key, obj):
    if not CONFIG["source"]["use_cache"]:
        return
    path = os.path.join(_CACHE_DIR, key + ".pkl")
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "wb") as f:
            pickle.dump(obj, f)
        os.replace(tmp, path)
    except Exception as e:
        log.debug("cache save failed %s: %s", key, e)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _retry(fn, *a, **k):
    f = CONFIG["fetch"]
    last = None
    for i in range(f["max_retries"]):
        try:
            time.sleep(f["sleep_sec"])
            return fn(*a, **k)
        except Exception as e:  # noqa
            last = e
            time.sleep(f["retry_backoff_sec"] * (2 ** i))
    raise last


# ===========================================================================
#  1) 股票池 + 板块 (标普500)
# ===========================================================================
# NASDAQ 板块名 -> GICS/SPDR 口径 (统一以便板块景气 ETF 匹配)
_NASDAQ_TO_GICS = {
    "Technology": "Information Technology",
    "Finance": "Financials",
    "Health Care": "Health Care",
    "Consumer Discretionary": "Consumer Discretionary",
    "Consumer Staples": "Consumer Staples",
    "Industrials": "Industrials",
    "Energy": "Energy",
    "Real Estate": "Real Estate",
    "Utilities": "Utilities",
    "Basic Materials": "Materials",
    "Telecommunications": "Communication Services",
}


def get_universe() -> pd.DataFrame | None:
    """返回 code, name, sector 的 DataFrame。
    模式 all_us: 市值>=下限的全美股 (NASDAQ 官方筛选器); sp500: 仅标普500。"""
    mode = CONFIG["source"]["universe_mode"]
    key = _cache_key("universe", mode, dt.date.today().isoformat())
    c = _cache_load(key)
    if c is not None:
        return c
    df = None
    if mode == "all_us":
        df = _universe_all_us()
    if df is None or df.empty:      # sp500 模式, 或 all_us 失败 -> 退回标普500
        df = _universe_from_csv()
        if df is None or df.empty:
            df = _universe_from_wiki()
    if df is None or df.empty:
        log.warning("在线名单获取失败, 使用内置兜底名单(%d只)", len(_FALLBACK_UNIVERSE))
        df = pd.DataFrame(_FALLBACK_UNIVERSE, columns=["code", "name", "sector"])
    df["code"] = df["code"].astype(str).str.strip()
    df = df.dropna(subset=["code"]).drop_duplicates(subset=["code"]).reset_index(drop=True)
    _cache_save(key, df)
    return df


def _clean_mcap(x) -> float:
    s = str(x).replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except Exception:
        return 0.0


def _valid_ticker(sym: str) -> bool:
    sym = str(sym).strip().upper()
    return bool(sym) and "^" not in sym and " " not in sym and all(
        ch.isalnum() or ch in ".-" for ch in sym)


def _universe_all_us() -> pd.DataFrame | None:
    import requests
    hdr = {**_UA, "Accept": "application/json, text/plain, */*",
           "Accept-Language": "en-US,en;q=0.9",
           "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
    try:
        r = _retry(requests.get, CONFIG["source"]["nasdaq_screener"], headers=hdr,
                   timeout=CONFIG["fetch"]["timeout_sec"])
        data = r.json().get("data", {})
        rows = data.get("rows") or (data.get("table") or {}).get("rows") or []
    except Exception as e:
        log.warning("NASDAQ 全美股名单失败: %s", e)
        return None
    if not rows:
        return None
    minmc = CONFIG["source"]["min_market_cap"]
    out = []
    for row in rows:
        sym = str(row.get("symbol", "")).strip()
        if not _valid_ticker(sym):
            continue
        if _clean_mcap(row.get("marketCap")) < minmc:
            continue
        sec_raw = str(row.get("sector", "")).strip()
        out.append((sym, str(row.get("name", "")).strip(), _NASDAQ_TO_GICS.get(sec_raw, sec_raw)))
    if not out:
        return None
    log.info("全美股(市值>=%.1gB): %d 只", minmc / 1e9, len(out))
    return pd.DataFrame(out, columns=["code", "name", "sector"])


def _universe_from_csv():
    import requests
    for url in CONFIG["source"]["sp500_csv"]:
        try:
            r = _retry(requests.get, url, headers=_UA, timeout=CONFIG["fetch"]["timeout_sec"])
            raw = pd.read_csv(io.StringIO(r.text))
            cols = {c.lower(): c for c in raw.columns}
            sym = cols.get("symbol")
            name = cols.get("security") or cols.get("name")
            sec = cols.get("gics sector") or cols.get("sector")
            if not sym:
                continue
            df = pd.DataFrame({
                "code": raw[sym],
                "name": raw[name] if name else raw[sym],
                "sector": raw[sec] if sec else "",
            })
            return df
        except Exception as e:
            log.debug("S&P500 CSV %s 失败: %s", url, e)
    return None


def _universe_from_wiki():
    import requests
    try:
        r = _retry(requests.get, CONFIG["source"]["sp500_wiki"], headers=_UA,
                   timeout=CONFIG["fetch"]["timeout_sec"])
        tabs = pd.read_html(io.StringIO(r.text))
        raw = tabs[0]
        return pd.DataFrame({
            "code": raw["Symbol"],
            "name": raw.get("Security", raw["Symbol"]),
            "sector": raw.get("GICS Sector", ""),
        })
    except Exception as e:
        log.debug("维基 S&P500 失败: %s", e)
        return None


# ===========================================================================
#  2) 个股日线 (yfinance, 复权)
# ===========================================================================
def _normalize_hist(raw: pd.DataFrame) -> pd.DataFrame | None:
    if raw is None or len(raw) == 0:
        return None
    df = raw.reset_index()
    ren = {}
    for c in df.columns:
        cl = str(c).lower()
        if cl in ("date", "datetime"):
            ren[c] = "date"
        elif cl == "open":
            ren[c] = "open"
        elif cl == "high":
            ren[c] = "high"
        elif cl == "low":
            ren[c] = "low"
        elif cl == "close":
            ren[c] = "close"
        elif cl == "volume":
            ren[c] = "volume"
    df = df.rename(columns=ren)
    need = {"date", "open", "high", "low", "close"}
    if not need.issubset(df.columns):
        return None
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    if "volume" in df.columns:
        df["amount"] = df["close"] * df["volume"]   # 成交额(美元) 用 收盘*成交量 近似
    df = df.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
    return df


def fetch_hist(code: str) -> pd.DataFrame | None:
    f = CONFIG["fetch"]
    key = _cache_key("hist", code, f["period"], dt.date.today().isoformat())
    c = _cache_load(key)
    if c is not None:
        return c
    try:
        tk = _yf().Ticker(_yf_symbol(code))
        raw = _retry(tk.history, period=f["period"], auto_adjust=f["auto_adjust"],
                     timeout=f["timeout_sec"])
    except Exception as e:
        log.debug("fetch_hist %s 失败: %s", code, e)
        return None
    df = _normalize_hist(raw)
    if df is None or df.empty:
        return None
    _cache_save(key, df)
    return df


# ===========================================================================
#  3) 个股基本面 / 板块 / 52周  (yf.info)
# ===========================================================================
def fetch_info(code: str) -> dict | None:
    key = _cache_key("info", code, dt.date.today().isoformat())
    c = _cache_load(key)
    if c is not None:
        return c
    def _get():   # 每次重试都用全新 Ticker, 促使 yfinance 重新取 crumb/cookie
        return _yf().Ticker(_yf_symbol(code)).info
    try:
        info = _retry(_get)
    except Exception as e:
        log.debug("fetch_info %s 失败: %s", code, e)
        return None
    if not isinstance(info, dict) or not info:
        return None
    _cache_save(key, info)
    return info


_NI_ROWS = ("Net Income", "Net Income Common Stockholders",
            "Net Income Including Noncontrolling Interests")
_EQ_ROWS = ("Stockholders Equity", "Total Stockholders Equity",
            "Common Stock Equity", "Total Equity Gross Minority Interest")


def fetch_roe_trend(code: str) -> list:
    """年度 ROE 趋势 = 净利润 / 股东权益 * 100 (yfinance 年报, 通常近4个财年)。
    取不到(接口失败/行名缺失/权益<=0)返回 [], 前端显示"暂无"。"""
    key = _cache_key("roetrend", code, dt.date.today().isoformat())
    c = _cache_load(key)
    if c is not None:
        return c if isinstance(c, list) else []

    def _row(df, names):
        if df is None or getattr(df, "empty", True):
            return None
        for n in names:
            if n in df.index:
                return df.loc[n]
        return None

    out = []
    try:
        tk = _yf().Ticker(_yf_symbol(code))
        inc = _retry(lambda: tk.income_stmt)
        bal = _retry(lambda: tk.balance_sheet)
        ni = _row(inc, _NI_ROWS)
        eq = _row(bal, _EQ_ROWS)
        if ni is not None and eq is not None:
            for col in ni.index:
                if col not in eq.index:
                    continue
                n_v, e_v = ni[col], eq[col]
                try:
                    n_v, e_v = float(n_v), float(e_v)
                except Exception:
                    continue
                if not (pd.notna(n_v) and pd.notna(e_v)) or e_v <= 0:
                    continue
                out.append({"date": pd.Timestamp(col).strftime("%Y-%m"),
                            "value": round(n_v / e_v * 100.0, 1)})
        out.sort(key=lambda d: d["date"])
        out = out[-5:]
    except Exception as e:
        log.debug("fetch_roe_trend %s 失败: %s", code, e)
        out = []
    _cache_save(key, out)
    return out


# ===========================================================================
#  4) 板块指数 (SPDR 行业 ETF) / 基准 (SPY)
# ===========================================================================
def fetch_sector_hist(sector: str) -> pd.DataFrame | None:
    etf = CONFIG["source"]["sector_etf"].get(sector)
    if not etf:
        return None
    return fetch_hist(etf)


def fetch_benchmark() -> pd.DataFrame | None:
    return fetch_hist(CONFIG["source"]["benchmark"])
