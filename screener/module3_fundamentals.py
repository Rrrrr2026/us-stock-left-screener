#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块3 — 基本面抓取 (美股, yfinance .info)
=========================================
从 yf.Ticker(t).info 取 PE/PB/ROE/EPS/增长/毛利/股息 等, 映射到与A股版一致的字段,
以复用 db/export/dashboard。板块PE中位对比由编排层传入。
缺失值 -> None (前端显示 —)。
"""
from __future__ import annotations
import logging
import numpy as np

from . import datasource as ds

log = logging.getLogger("screener.module3")


def _num(x):
    if x is None:
        return None
    try:
        v = float(x)
        return None if (np.isnan(v) or np.isinf(v)) else v
    except Exception:
        return None


def _pct(x, scale=100.0):
    """yfinance 的比率多为小数(0.15=15%), 统一 ×100 成百分数。"""
    v = _num(x)
    return None if v is None else round(v * scale, 2)


def pull_fundamentals(code: str, sector: str | None = None,
                      sector_pe_median: float | None = None) -> dict:
    res = {
        "pe_ttm": None, "pe_pct": None, "pe_industry_median": None, "pe_vs_industry": None,
        "pb": None, "pb_pct": None, "dividend_yield": None,
        "eps": None, "eps_yoy": None, "roe": None, "roe_trend": [],
        "revenue_yoy": None, "netprofit_yoy": None, "gross_margin": None, "debt_ratio": None,
        "target_price": None, "analyst_rating": None, "analyst_count": None, "upside_pct": None,
        "fund_flags": [],
    }
    info = ds.fetch_info(code)
    if not info:
        res["fund_flags"] = _flags(res)
        return res

    # 分析师目标价 / 评级 / 上涨空间
    res["target_price"] = _num(info.get("targetMeanPrice"))
    res["analyst_count"] = _num(info.get("numberOfAnalystOpinions"))
    rk = info.get("recommendationKey")
    _RK = {"strong_buy": "强力买入", "buy": "买入", "hold": "持有",
           "sell": "卖出", "strong_sell": "强力卖出", "underperform": "跑输", "outperform": "跑赢"}
    res["analyst_rating"] = _RK.get(str(rk), rk) if rk and rk != "none" else None
    cur = _num(info.get("currentPrice")) or _num(info.get("regularMarketPrice"))
    if res["target_price"] and cur and cur > 0:
        res["upside_pct"] = round((res["target_price"] / cur - 1.0) * 100.0, 1)

    res["pe_ttm"] = _num(info.get("trailingPE"))
    res["pb"] = _num(info.get("priceToBook"))
    res["roe"] = _pct(info.get("returnOnEquity"))
    res["eps"] = _num(info.get("trailingEps"))
    res["revenue_yoy"] = _pct(info.get("revenueGrowth"))
    res["netprofit_yoy"] = _pct(info.get("earningsGrowth"))
    res["eps_yoy"] = _pct(info.get("earningsQuarterlyGrowth")) or res["netprofit_yoy"]
    res["gross_margin"] = _pct(info.get("grossMargins"))

    # 杠杆: yfinance 无"资产负债率", 用 债务/(债务+权益) 近似 (debtToEquity 是百分比数)
    dte = _num(info.get("debtToEquity"))
    if dte is not None and dte >= 0:
        res["debt_ratio"] = round(dte / (dte + 100.0) * 100.0, 1)

    # 股息率: 不同 yf 版本可能是小数或百分数
    dy = _num(info.get("dividendYield"))
    if dy is not None:
        res["dividend_yield"] = round(dy * 100.0, 2) if dy < 1 else round(dy, 2)

    # ROE 多年趋势 (年度财报: 净利润/股东权益; 失败静默为 []) + 季度TTM口径
    try:
        res["roe_trend"] = ds.fetch_roe_trend(code)
    except Exception:
        pass
    try:
        res["roe_trend_q"] = ds.fetch_roe_trend_q(code)
    except Exception:
        pass

    # 板块 PE 中位对比
    if sector_pe_median is not None and res["pe_ttm"] is not None and sector_pe_median > 0:
        res["pe_industry_median"] = round(float(sector_pe_median), 2)
        res["pe_vs_industry"] = round(res["pe_ttm"] / sector_pe_median, 2)

    res["fund_flags"] = _flags(res)
    return res


def _flags(r: dict) -> list:
    flags = []
    if r.get("roe") is not None:
        if r["roe"] >= 18:
            flags.append("高ROE")
        elif r["roe"] < 0:
            flags.append("⚠️亏损/负ROE")
    if r.get("pe_ttm") is not None and r["pe_ttm"] <= 0:
        flags.append("⚠️PE为负(亏损)")
    if r.get("netprofit_yoy") is not None:
        if r["netprofit_yoy"] > 0:
            flags.append("盈利正增长")
        elif r["netprofit_yoy"] < -20:
            flags.append("⚠️盈利下滑")
    if r.get("gross_margin") is not None and r["gross_margin"] >= 40:
        flags.append("高毛利")
    if r.get("debt_ratio") is not None and r["debt_ratio"] >= 70:
        flags.append("⚠️高杠杆")
    return flags
