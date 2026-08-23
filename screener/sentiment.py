#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块11 — 🌡️ 市场风险偏好指数 (S&P 500 Risk Appetite)
=====================================================
自上而下的"市场在 risk-on 还是 risk-off"判断, 与自下而上的机会温度计互补。

五大支柱 (各自在近3年历史里的百分位, 方向统一为"越高越敢冒险"):
  恐慌 (30%)  VIX 水平(反向) / VIX 10日变化(反向) / VVIX(反向) / SKEW(反向)
             (CBOE 期限结构系列 ^VIX3M/^VIX9D 在 Yahoo 上 2026-07-17 起停更, 故未用)
  信用 (15%)  HYG/IEF 20日相对动量 —— 信用利差通常先于股市嗅到真正的压力
  广度 (25%)  我们自己数据库里 ~4000 只美股: 站上50/200日均线占比、20日新高-新低
  领涨 (20%)  周期/成长(XLK+XLY+XLF) vs 防御(XLU+XLP+XLV) 20日相对动量; 等权 vs 市值(RSP/SPY)
  避险 (10%)  黄金 / 长债 / 美元 20日动量 (反向)

阶段机 (用户的论点: 刚开始的恐慌 -> 还会跌; 恐慌很久且开始缓解 -> 可能见底):
  acute            VIX>=30 且处于10日高点附近 (新鲜恐慌, 仍在升温)
  prolonged        VIX>=25 已连续>=10日, 尚未明显回落
  prolonged_easing VIX>=25 已连续>=10日 且 已从20日峰值回落>=20%  (历史上的"底部区")
  elevated         VIX>=20
  calm / complacent  其余, 按指数高低区分
每个阶段都用 1990 年以来的真实数据给出"随后 1/3/6 个月 标普收益"的分布 —— 论点可证伪。

⚠️ 指数是相对排位 + 历史基率, 不是预测; 样本窗口按每5个交易日抽取以减少重叠。
"""
from __future__ import annotations
import datetime as dt
import json
import logging
import os
import pickle

import numpy as np
import pandas as pd

from .config import DASHBOARD_DIR, DATA_DIR, CONFIG

log = logging.getLogger("screener.sentiment")

OUT_JS = os.path.join(DASHBOARD_DIR, "sentiment_data.js")
OUT_JSON = os.path.join(DATA_DIR, "sentiment_result.json")
CACHE_DIR = os.path.join(DATA_DIR, "cache")

TICKERS = ["^VIX", "^GSPC", "^VVIX", "^SKEW", "HYG", "IEF", "TLT", "GLD", "DX-Y.NYB",
           "RSP", "SPY", "XLK", "XLF", "XLV", "XLY", "XLP", "XLE", "XLI", "XLU", "XLB", "XLRE", "XLC"]
SECTORS = {"XLK": "科技", "XLY": "可选消费", "XLF": "金融", "XLC": "通信", "XLI": "工业",
           "XLB": "材料", "XLE": "能源", "XLV": "医疗", "XLP": "必需消费", "XLU": "公用", "XLRE": "地产"}
CYCLICAL = ["XLK", "XLY", "XLF"]
DEFENSIVE = ["XLU", "XLP", "XLV"]
WINDOW = 756              # 百分位基准: 近3年
FWD = (21, 63, 126)
STRIDE = 5
WEIGHTS = {"fear": 0.30, "credit": 0.15, "breadth": 0.25, "leader": 0.20, "safety": 0.10}


# ---------------------------------------------------------------------------
def load_prices() -> pd.DataFrame:
    """收盘价宽表 (列=ticker) + 成交量 (列= ticker+'_vol'); 按天缓存。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = os.path.join(CACHE_DIR, f"sentiment_px_{dt.date.today().isoformat()}.pkl")
    if os.path.exists(p):
        return pickle.load(open(p, "rb"))
    import yfinance as yf
    raw = yf.download(TICKERS, start="1990-01-01", auto_adjust=True, progress=False,
                      group_by="ticker", threads=True)
    cols = {}
    for t in TICKERS:
        try:
            cols[t] = raw[t]["Close"]
            cols[t + "_vol"] = raw[t]["Volume"]
        except Exception:
            pass
    df = pd.DataFrame(cols)
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df = df.sort_index()
    pickle.dump(df, open(p, "wb"))
    return df


def _pct_rank(series: pd.Series, window: int = WINDOW) -> pd.Series:
    """每个时点相对其前 window 根 (含自身) 的百分位 (0-100)。"""
    def _last_pct(x):
        v = x[-1]
        return float((x < v).sum() + 0.5 * (x == v).sum()) / len(x) * 100.0
    return series.rolling(window, min_periods=max(60, window // 4)).apply(_last_pct, raw=True)


def _ret(series: pd.Series, n: int) -> pd.Series:
    return series / series.shift(n) - 1.0


# ---------------------------------------------------------------------------
#  广度: 自家数据库的全股票池日线缓存 (无网络)
# ---------------------------------------------------------------------------
def breadth_from_cache(max_days_back: int = 5) -> pd.DataFrame | None:
    from . import datasource as ds
    try:
        uni = ds.get_universe()
    except Exception as e:
        log.warning("股票池不可得, 跳过广度: %s", e)
        return None
    if uni is None or uni.empty:
        return None
    period = CONFIG["fetch"]["period"]
    codes = [str(c) for c in uni["code"]]
    above50, above200, hi20, lo20 = {}, {}, {}, {}
    n_ok = 0
    for code in codes:
        df = None
        for back in range(max_days_back + 1):
            d = (dt.date.today() - dt.timedelta(days=back)).isoformat()
            path = os.path.join(CACHE_DIR, ds._cache_key("hist", code, period, d) + ".pkl")
            if os.path.exists(path):
                try:
                    df = pickle.load(open(path, "rb"))
                except Exception:
                    df = None
                break
        if df is None or len(df) < 210:
            continue
        ccol = "close" if "close" in df.columns else ("Close" if "Close" in df.columns else None)
        if ccol is None:
            continue
        c = df[ccol].astype(float)
        idx = pd.to_datetime(df["date"]) if "date" in df.columns else pd.to_datetime(df.index)
        c.index = pd.DatetimeIndex(idx).tz_localize(None)
        c = c[~c.index.duplicated()].sort_index()
        ma50, ma200 = c.rolling(50).mean(), c.rolling(200).mean()
        rh, rl = c.rolling(20).max(), c.rolling(20).min()
        tail = c.index[-270:]
        above50[code] = (c > ma50).reindex(tail)
        above200[code] = (c > ma200).reindex(tail)
        hi20[code] = (c >= rh).reindex(tail)
        lo20[code] = (c <= rl).reindex(tail)
        n_ok += 1
    if n_ok < 200:
        log.warning("广度样本不足 (%d 只有缓存日线), 跳过广度支柱", n_ok)
        return None
    A50, A200, H, L = (pd.DataFrame(d) for d in (above50, above200, hi20, lo20))
    cnt = A50.notna().sum(axis=1)
    ok = cnt >= max(200, 0.5 * n_ok)
    out = pd.DataFrame({
        "pct50": A50.mean(axis=1) * 100.0, "pct200": A200.mean(axis=1) * 100.0,
        "nh_nl": (H.sum(axis=1) - L.sum(axis=1)) / cnt.replace(0, np.nan) * 100.0,
        "n": cnt,
    })[ok]
    log.info("广度: %d 只股票, %d 个交易日", n_ok, len(out))
    return out


# ---------------------------------------------------------------------------
#  阶段机 + 历史验证
# ---------------------------------------------------------------------------
def vix_states(vix: pd.Series) -> pd.Series:
    v = vix.astype(float)
    above25 = (v >= 25).astype(int)
    run = above25.groupby((above25 != above25.shift()).cumsum()).cumsum() * above25
    max10 = v.rolling(10).max()
    max20 = v.rolling(20).max()
    st = pd.Series("calm", index=v.index)
    st[v >= 20] = "elevated"
    st[(run >= 10)] = "prolonged"
    st[(run >= 10) & (v <= 0.8 * max20)] = "prolonged_easing"
    st[(v >= 30) & (v >= 0.95 * max10)] = "acute"
    return st


def forward_table(spx: pd.Series, groups: pd.Series, label: str) -> list:
    """各组别随后 21/63/126 日收益分布 (每5日抽样减少重叠)。"""
    out = []
    idx = spx.index
    for g in sorted(groups.dropna().unique(), key=str):
        pos = np.where(groups.values == g)[0]
        pos = pos[::STRIDE]
        row = {"group": str(g), "n": int(len(pos))}
        for h in FWD:
            ok = pos[pos + h < len(idx)]
            if len(ok) < 5:
                row[f"r{h}"] = None
                continue
            r = spx.values[ok + h] / spx.values[ok] - 1.0
            row[f"r{h}"] = {"mean": round(float(np.mean(r)) * 100, 2),
                            "median": round(float(np.median(r)) * 100, 2),
                            "pos": round(float(np.mean(r > 0)) * 100, 1),
                            "p10": round(float(np.percentile(r, 10)) * 100, 2),
                            "n": int(len(ok))}
        out.append(row)
    return {"label": label, "rows": out}


def validate(vix: pd.Series, spx: pd.Series) -> dict:
    df = pd.concat([vix.rename("vix"), spx.rename("spx")], axis=1).dropna()
    bins = pd.cut(df["vix"], [0, 15, 20, 25, 30, 40, 200],
                  labels=["<15", "15-20", "20-25", "25-30", "30-40", ">40"])
    t1 = forward_table(df["spx"], bins.astype(str), "按VIX水平")
    st = vix_states(df["vix"])
    t2 = forward_table(df["spx"], st, "按恐慌阶段")
    # 经典信号: VIX 从52周高点回落 >=30% 且 52周高点 >= 35
    hi252 = df["vix"].rolling(252).max()
    sig = ((hi252 >= 35) & (df["vix"] <= 0.7 * hi252) & (df["vix"].shift(1) > 0.7 * hi252.shift(1)))
    t3 = forward_table(df["spx"], sig.map({True: "VIX自峰值回落30%(峰>=35)", False: "其它日子"}), "经典见底信号")
    base = forward_table(df["spx"], pd.Series("全部日子", index=df.index), "基准")
    return {"by_level": t1, "by_state": t2, "classic": t3, "base": base,
            "since": str(df.index[0].date()), "until": str(df.index[-1].date())}


# ---------------------------------------------------------------------------
#  指数构建
# ---------------------------------------------------------------------------
def build() -> dict | None:
    px = load_prices()
    if px is None or px.empty or "^VIX" not in px:
        log.warning("行情不可得, 跳过风险偏好指数")
        return None
    vix, spx = px["^VIX"].dropna(), px["^GSPC"].dropna()
    parts = {}
    # 恐慌 (反向)
    parts["fear"] = pd.concat([
        100 - _pct_rank(vix),
        100 - _pct_rank(vix - vix.shift(10)),
        100 - _pct_rank(px["^VVIX"]) if "^VVIX" in px else None,
        100 - _pct_rank(px["^SKEW"]) if "^SKEW" in px else None,
    ], axis=1).mean(axis=1)
    # 信用
    parts["credit"] = _pct_rank(_ret(px["HYG"] / px["IEF"], 20))
    # 领涨
    cyc = px[CYCLICAL].mean(axis=1)
    dfn = px[DEFENSIVE].mean(axis=1)
    parts["leader"] = pd.concat([_pct_rank(_ret(cyc / dfn, 20)), _pct_rank(_ret(px["RSP"] / px["SPY"], 20))],
                                axis=1).mean(axis=1)
    # 避险 (反向)
    parts["safety"] = pd.concat([100 - _pct_rank(_ret(px["GLD"], 20)), 100 - _pct_rank(_ret(px["TLT"], 20)),
                                 100 - _pct_rank(_ret(px["DX-Y.NYB"], 20))], axis=1).mean(axis=1)
    # 广度 (自家缓存)
    br = None
    try:
        br = breadth_from_cache()
    except Exception as e:
        log.warning("广度计算失败: %s", e)
    weights = dict(WEIGHTS)
    if br is not None and len(br) >= 120:
        brw = min(len(br), WINDOW)
        b = pd.concat([_pct_rank(br["pct50"], brw), _pct_rank(br["pct200"], brw), _pct_rank(br["nh_nl"], brw)],
                      axis=1).mean(axis=1)
        # 自家日线缓存比行情晚一个交易日 (早晨抓的是前一日收盘): 前向填充 <=3 日对齐
        parts["breadth"] = b.reindex(b.index.union(px.index)).ffill(limit=3)
    else:
        weights.pop("breadth")
        tot = sum(weights.values())
        weights = {k: v / tot for k, v in weights.items()}
    comp = pd.DataFrame(parts)
    score = sum(comp[k] * w for k, w in weights.items() if k in comp) / sum(w for k, w in weights.items() if k in comp)
    score = score.dropna()
    states = vix_states(vix)
    last = score.index[-1]

    def _phase(d):
        s = states.get(d, "calm")
        if s in ("acute", "prolonged", "prolonged_easing", "elevated"):
            return s
        return "complacent" if score.get(d, 50) >= 75 else "calm"

    # 两周逐日
    two = []
    for d in score.index[-11:]:
        two.append({"d": str(d.date()), "score": round(float(score[d]), 1), "vix": round(float(vix.get(d, np.nan)), 2),
                    "spx": round(float(spx.get(d, np.nan)), 2), "phase": _phase(d),
                    "pct50": (round(float(br["pct50"].get(d, np.nan)), 1) if br is not None and d in br.index else None)})
    # 板块兴趣: 10日收益 / 相对SPY / 相对成交量
    sectors = []
    spy10 = float(_ret(px["SPY"], 10).iloc[-1])
    for t, nm in SECTORS.items():
        if t not in px:
            continue
        s = px[t].dropna()
        v = px.get(t + "_vol")
        r10 = float(_ret(s, 10).iloc[-1]) if len(s) > 10 else None
        rv = (float(v.tail(10).mean() / v.tail(60).mean()) if v is not None and v.tail(60).mean() > 0 else None)
        sectors.append({"etf": t, "name": nm, "r10": round(r10 * 100, 2) if r10 is not None else None,
                        "rel10": round((r10 - spy10) * 100, 2) if r10 is not None else None,
                        "rvol": round(rv, 2) if rv is not None else None,
                        "above50": bool(s.iloc[-1] > s.rolling(50).mean().iloc[-1]),
                        "above200": bool(s.iloc[-1] > s.rolling(200).mean().iloc[-1])})
    sectors.sort(key=lambda x: -(x["rel10"] if x["rel10"] is not None else -99))
    # 我们榜单里的板块分布 (左侧兴趣 = 哪些板块正在被抛售到支撑)
    board = {}
    try:
        raw = open(os.path.join(DASHBOARD_DIR, "dashboard_data.js"), encoding="utf-8").read()
        j = json.loads(raw[raw.index("=") + 1:].rstrip().rstrip(";"))
        for c in j.get("candidates", []):
            k = c.get("industry") or "?"
            board.setdefault(k, {"n": 0, "cs": 0})
            board[k]["n"] += 1
            if c.get("cuosha_score"):
                board[k]["cs"] += 1
    except Exception:
        pass
    val = validate(vix, spx)
    cur = {k: round(float(comp.loc[last, k]), 1) for k in weights if k in comp and pd.notna(comp.loc[last, k])}
    vix_now = float(vix.iloc[-1])
    # 连续>=25日数 (从最新一天往回数)
    n25 = 0
    for x in vix.values[::-1]:
        if x >= 25:
            n25 += 1
        else:
            break
    result = {
        "meta": {"date": str(last.date()), "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                 "weights": weights, "window_days": WINDOW, "breadth_n": (int(br["n"].iloc[-1]) if br is not None else 0),
                 "term_structure": "unavailable (Yahoo ^VIX3M/^VIX9D stale since 2026-07-17)"},
        "score": round(float(score.iloc[-1]), 1), "phase": _phase(last), "pillars": cur,
        "vix": {"now": round(vix_now, 2), "pct3y": round(float(_pct_rank(vix).iloc[-1]), 1),
                "max20": round(float(vix.tail(20).max()), 2), "max252": round(float(vix.tail(252).max()), 2),
                "days_ge25": n25, "chg10": round(float(vix_now - vix.iloc[-11]), 2)},
        "spx": {"now": round(float(spx.iloc[-1]), 2), "r10": round(float(_ret(spx, 10).iloc[-1]) * 100, 2),
                "from_52w_high": round(float(spx.iloc[-1] / spx.tail(252).max() - 1) * 100, 2)},
        "two_weeks": two, "sectors": sectors, "board_sectors": board,
        "history": [{"d": str(d.date()), "s": round(float(score[d]), 1)} for d in score.index[-130:]],
        "validation": val,
    }
    json.dump(result, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False)
    with open(OUT_JS, "w", encoding="utf-8") as f:
        f.write("window.__SENT__ = ")
        json.dump(result, f, ensure_ascii=False)
        f.write(";\n")
    log.info("风险偏好指数: %.1f (%s) VIX=%.2f 广度样本=%d", result["score"], result["phase"], vix_now,
             result["meta"]["breadth_n"])
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    r = build()
    if r:
        print(json.dumps({k: r[k] for k in ("score", "phase", "pillars", "vix", "spx")}, ensure_ascii=False, indent=1))
