#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块1 — 板块景气度 (Sector Prosperity) —— 美股版
=================================================
对 11 个 GICS 板块算景气分 (用对应 SPDR 行业 ETF 作板块指数代理)。
支柱: 趋势 / 动量 (ETF) + 广度 (板块成分股在MA60上方比例等) + 基本面(暂略) + 资金(略)。
输出列沿用 A股版命名 (industry=板块名), 以复用 db/export/dashboard。
"""
from __future__ import annotations
import logging
import os as _os
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import pandas as pd

from .config import CONFIG
from . import datasource as ds
from . import indicators as ind
from .statutil import zscore, cross_sectional_percentile, safe_div

log = logging.getLogger("screener.module1")


def _ret(close: pd.Series, bars: int) -> float:
    s = close.dropna()
    if len(s) <= bars:
        return np.nan
    return float(s.iloc[-1] / s.iloc[-1 - bars] - 1.0)


def _sector_features(hist: pd.DataFrame, bench_ret60: float) -> dict:
    close = hist["close"].astype(float)
    px = close.iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]
    ma120 = close.rolling(120).mean().iloc[-1]
    m2 = _ret(close, 60)
    return {
        "idx_close": float(px),
        "ma120": float(ma120) if not np.isnan(ma120) else np.nan,
        "above_ma120": (not np.isnan(ma120)) and px >= ma120,
        "t1": safe_div(px - ma60, ma60), "t2": safe_div(px - ma120, ma120),
        "t3": ind.reg_slope_norm(close, 60),
        "m1": _ret(close, 20), "m2": m2,
        "m3": (m2 - bench_ret60) if (not np.isnan(m2) and not np.isnan(bench_ret60)) else np.nan,
    }


def _breadth(codes, sample: int) -> dict:
    codes = list(codes)[:sample]
    above_ma60, pos20, adv, dec, n = 0, 0, 0, 0, 0
    for code in codes:
        h = ds.fetch_hist(code)
        if h is None or len(h) < 65:
            continue
        c = h["close"].astype(float)
        px = c.iloc[-1]
        ma60 = c.rolling(60).mean().iloc[-1]
        if not np.isnan(ma60):
            above_ma60 += 1 if px >= ma60 else 0
        r20 = _ret(c, 20)
        if not np.isnan(r20):
            pos20 += 1 if r20 > 0 else 0
        r5 = _ret(c, 5)
        if not np.isnan(r5):
            adv += 1 if r5 > 0 else 0
            dec += 1 if r5 < 0 else 0
        n += 1
    if n == 0:
        return {"b1": np.nan, "b2": np.nan, "b3": np.nan, "n": 0}
    return {"b1": above_ma60 / n, "b2": pos20 / n, "b3": safe_div(adv - dec, n, 0.0), "n": n}


def compute_sector_scores(universe: pd.DataFrame, progress_cb=None) -> pd.DataFrame:
    """universe: 含 code, sector 列 (标普500)。返回板块景气 DataFrame。"""
    cfg = CONFIG["sector"]
    etf_map = CONFIG["source"]["sector_etf"]
    sectors = [s for s in universe["sector"].dropna().unique() if s in etf_map]
    if not sectors:
        return pd.DataFrame()

    bench = ds.fetch_benchmark()
    bench_ret60 = np.nan
    if bench is not None and len(bench) > 61:
        bc = bench["close"].astype(float)
        bench_ret60 = float(bc.iloc[-1] / bc.iloc[-61] - 1.0)

    sec_codes = {s: list(universe[universe["sector"] == s]["code"]) for s in sectors}

    def _one(sector):
        hist = ds.fetch_sector_hist(sector)
        if hist is None or len(hist) < 130:
            return None
        feat = _sector_features(hist, bench_ret60)
        br = _breadth(sec_codes.get(sector, []), cfg["breadth_sample"])
        return {"industry": sector, **feat,
                "b1": br["b1"], "b2": br["b2"], "b3": br["b3"], "breadth_n": br["n"]}

    workers = CONFIG["fetch"].get("max_workers") or min(12, (_os.cpu_count() or 4) * 2)
    rows, done, total = [], 0, len(sectors)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_one, s): s for s in sectors}
        for fut in as_completed(futs):
            done += 1
            if progress_cb:
                progress_cb(done, total, futs[fut])
            try:
                r = fut.result()
            except Exception as e:
                log.debug("板块 %s 失败: %s", futs[fut], e)
                r = None
            if r:
                rows.append(r)
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["A_raw"] = pd.concat([zscore(df["t1"]), zscore(df["t2"]), zscore(df["t3"])],
                            axis=1).mean(axis=1, skipna=True)
    df["B_raw"] = pd.concat([zscore(df["m1"]), zscore(df["m2"]), zscore(df["m3"])],
                            axis=1).mean(axis=1, skipna=True)
    b3s = (df["b3"] + 1.0) / 2.0
    df["C_raw"] = pd.concat([df["b1"], df["b2"], b3s], axis=1).mean(axis=1, skipna=True)

    df["trend"] = cross_sectional_percentile(df["A_raw"], fill=None)
    df["momentum"] = cross_sectional_percentile(df["B_raw"], fill=None)
    df["breadth"] = cross_sectional_percentile(df["C_raw"], fill=None)
    df["capital"] = np.nan       # 美股免费源无板块资金流, 权重按行重分配
    df["fundamental"] = np.nan

    weights = cfg["weights"]

    def _score_row(r):
        num, den = 0.0, 0.0
        for pillar, wt in weights.items():
            pct = r.get(pillar)
            if wt > 0 and pct is not None and not (isinstance(pct, float) and np.isnan(pct)):
                num += wt * (pct / 100.0)
                den += wt
        return round(100.0 * num / den, 2) if den > 0 else np.nan

    df["prosperity_score"] = df.apply(_score_row, axis=1)

    if cfg["trend_gate_enabled"]:
        tol = cfg["trend_gate_tolerance_pct"] / 100.0
        df["eligible"] = df.apply(
            lambda r: (not np.isnan(r["ma120"])) and r["idx_close"] >= r["ma120"] * (1 - tol), axis=1)
    else:
        df["eligible"] = True

    df = df.sort_values("prosperity_score", ascending=False).reset_index(drop=True)
    if cfg["use_full_universe"]:
        df["selected"] = df["eligible"]
    else:
        elig = df[df["eligible"]].head(cfg["top_n"])
        df["selected"] = df["industry"].isin(set(elig["industry"]))

    cols = ["industry", "prosperity_score", "trend", "momentum", "breadth", "capital",
            "fundamental", "idx_close", "ma120", "above_ma120", "eligible", "selected", "breadth_n"]
    return df[[c for c in cols if c in df.columns]]
