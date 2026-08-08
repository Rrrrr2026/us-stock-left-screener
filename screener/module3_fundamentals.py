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
        "fcf_yield": None, "sector_yf": None,
        "ni_ttm_yoy": None, "ni_parent_ttm_yoy": None, "ni_basis": None,
        "ni_parent_basis": None, "growth_quality": None,
        "ni_qoq": [], "ni_parent_qoq": [], "ni_q_labels": [],
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
    eq = _pct(info.get("earningsQuarterlyGrowth"))
    res["eps_yoy"] = eq if eq is not None else res["netprofit_yoy"]   # 0.0 是合法值, 不能用 or
    res["gross_margin"] = _pct(info.get("grossMargins"))

    # Yahoo 板块 (GICS口径) — 覆盖 NASDAQ 名单里的错误/缺失板块 (MO≠Health Care 等)
    sec_yf = info.get("sector")
    res["sector_yf"] = ds.YAHOO_TO_GICS.get(str(sec_yf).strip()) if sec_yf else None

    # FCF收益率 = 自由现金流 / 市值 (估值质量信号)
    fcf, mcap = _num(info.get("freeCashflow")), _num(info.get("marketCap"))
    if fcf is not None and mcap and mcap > 0:
        res["fcf_yield"] = round(fcf / mcap * 100.0, 2)

    # 杠杆: yfinance 无"资产负债率", 用 债务/(债务+权益) 近似 (debtToEquity 是百分比数)
    dte = _num(info.get("debtToEquity"))
    if dte is not None and dte >= 0:
        res["debt_ratio"] = round(dte / (dte + 100.0) * 100.0, 1)

    # 股息率: 首选 年股息$/现价 (单位无歧义); 兜底才用 dividendYield —
    # yfinance>=0.2.54 起 dividendYield 已是百分数(0.96=0.96%), 老代码的
    # "dy<1 则 ×100" 启发式会把真实<1%的股息率放大100倍 (SPGI 0.96%→94%的根因)
    rate = _num(info.get("dividendRate")) or _num(info.get("trailingAnnualDividendRate"))
    if rate and cur and cur > 0:
        res["dividend_yield"] = round(rate / cur * 100.0, 2)
    else:
        dy = _num(info.get("dividendYield"))
        if dy is not None:
            tay = _num(info.get("trailingAnnualDividendYield"))   # 恒为小数口径, 用作单位判别
            if tay is not None and tay > 0:
                # dy 与 tay*100 同量级 => dy已是百分数; 与 tay 同量级 => dy是小数
                res["dividend_yield"] = round(dy, 2) if abs(dy - tay * 100.0) < abs(dy - tay) else round(dy * 100.0, 2)
            else:
                res["dividend_yield"] = round(dy, 2)              # 新版口径: 按百分数处理
    if res["dividend_yield"] is not None and not (0 <= res["dividend_yield"] <= 30):
        res["dividend_yield"] = None      # >30% 基本是单位错乱/特殊分配, 宁缺毋滥

    # 近四季净利/归母净利增速 (分层口径: 8季全→真TTM同比; 否则 单季同比; 再退 年度同比)
    # + 增长持续性 (年度趋势×季度环比 交叉判断: 持续增长/拐点向上/单季脉冲/…)
    try:
        qni = ds.fetch_quarterly_ni(code)
    except Exception:
        qni = {}
    res["ni_ttm_yoy"], res["ni_basis"] = _tiered_yoy(
        qni.get("ni"), qni.get("fy_ni"), qni.get("q_dates"), qni.get("fy_dates"))
    res["ni_parent_ttm_yoy"], res["ni_parent_basis"] = _tiered_yoy(
        qni.get("ni_parent"), qni.get("fy_ni_parent"), qni.get("q_dates"), qni.get("fy_dates"))
    res["growth_quality"] = _growth_quality(qni)
    # 逐季环比序列 (最近4个, 旧→新) — Yahoo 仅给~5个季度, 同比×4 无数据支撑, 环比×4 可行
    res["ni_qoq"], res["ni_q_labels"] = _qoq_series(qni.get("ni"), qni.get("q_dates"))
    res["ni_parent_qoq"], _ = _qoq_series(qni.get("ni_parent"), qni.get("q_dates"))

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


def _yoy_pct(now, prev):
    """负基数用 |prev| 作分母 (亏转盈得正增速), 基数近零不计。"""
    if now is None or prev is None or abs(prev) < 1e-9:
        return None
    return round((now - prev) / abs(prev) * 100.0, 1)


def _gap_days(d1: str | None, d2: str | None):
    """'YYYY-MM' 字符串间隔天数; 解析失败返回 None。"""
    try:
        import datetime as _dt
        a = _dt.datetime.strptime(d1, "%Y-%m")
        b = _dt.datetime.strptime(d2, "%Y-%m")
        return abs((b - a).days)
    except Exception:
        return None


def _tiered_yoy(qv: list | None, fyv: list | None,
                q_dates: list | None = None, fy_dates: list | None = None):
    """净利增速的分层口径: (增速%, 口径标签)。带财报期连续性校验 —
    Yahoo 偶有缺季/半年报期, 纯按位置配对会算错同比。
    ① 8个季度齐且相邻间隔≤120天 → 近4季合计 vs 前4季合计 (真TTM同比)
    ② ≥5季且 q[-1] 与 q[-5] 间隔≈1年(300~430天) → 单季同比
    ③ 年报≥2年且相邻间隔≈1年 → 最近财年同比 (年度)"""
    qv = qv or []
    fyv = fyv or []
    q_dates = q_dates or []
    if len(qv) >= 8 and all(v is not None for v in qv[-8:]):
        gaps_ok = True
        if len(q_dates) == len(qv):
            for i in range(len(qv) - 8, len(qv) - 1):
                g = _gap_days(q_dates[i], q_dates[i + 1])
                if g is None or g > 120:
                    gaps_ok = False
                    break
        if gaps_ok:
            y = _yoy_pct(sum(qv[-4:]), sum(qv[-8:-4]))
            if y is not None:
                return y, "TTM"
    if len(qv) >= 5 and qv[-1] is not None and qv[-5] is not None:
        span_ok = True
        if len(q_dates) == len(qv):
            g = _gap_days(q_dates[-5], q_dates[-1])
            span_ok = g is not None and 300 <= g <= 430
        if span_ok:
            y = _yoy_pct(qv[-1], qv[-5])
            if y is not None:
                return y, "单季"
    pairs = [(d, v) for d, v in zip(fy_dates or [None] * len(fyv), fyv) if v is not None]
    if len(pairs) >= 2:
        (d_prev, v_prev), (d_now, v_now) = pairs[-2], pairs[-1]
        g = _gap_days(d_prev, d_now) if (d_prev and d_now) else 365
        if g is not None and 300 <= g <= 430:
            y = _yoy_pct(v_now, v_prev)
            if y is not None:
                return y, "年度"
    return None, None


def _q_label(d: str | None) -> str | None:
    """'2026-06' -> '26Q2' (按月份粗归到自然季)。"""
    try:
        y, m = d.split("-")
        return f"{y[2:]}Q{(int(m) - 1) // 3 + 1}"
    except Exception:
        return d


def _qoq_series(qv: list | None, q_dates: list | None, k: int = 4):
    """相邻季度环比增速序列 (最近k个, 旧→新) + 对应季度标签。
    相邻两期间隔>120天(缺季/半年报)的环比不成立, 记 None。"""
    qv = qv or []
    q_dates = q_dates or []
    out, labels = [], []
    for i in range(1, len(qv)):
        g = _gap_days(q_dates[i - 1], q_dates[i]) if len(q_dates) == len(qv) else 92
        val = _yoy_pct(qv[i], qv[i - 1]) if (g is not None and g <= 120) else None
        out.append(val)
        labels.append(_q_label(q_dates[i]) if i < len(q_dates) else None)
    return out[-k:], labels[-k:]


def _growth_quality(qni: dict) -> str | None:
    """增长持续性: 用 年度净利趋势(近4财年) × 季度环比(近4个) 交叉判断,
    区分"真持续增长"与"单季脉冲式放量"。"""
    q = qni.get("ni") or []
    fyv = [v for v in (qni.get("fy_ni") or []) if v is not None]
    if not q and not fyv:
        return None
    ann_up = sum(1 for i in range(1, len(fyv)) if fyv[i] > fyv[i - 1])
    qq, prev = [], None
    for v in q:
        if v is None:
            continue
        if prev is not None:
            qq.append(v > prev)
        prev = v
    q_recent = qq[-4:]
    q_up, q_n = sum(1 for b in q_recent if b), len(q_recent)
    yoy, _ = _tiered_yoy(q, qni.get("fy_ni"))
    if yoy is None or q_n == 0:
        return "数据不足"
    if yoy > 0 and ann_up >= 2 and q_up >= 2:
        return "持续增长"          # 年度在涨 + 季度多数在涨 -> 可持续
    if yoy > 0 and q_up >= 2:
        return "拐点向上"          # 年度还没证明, 但季度连续改善
    if yoy > 0:
        return "单季脉冲"          # 只有一根季度放量, 谨防一次性损益
    if ann_up >= 2:
        return "增速回落"          # 长期在涨但最近转负
    return "增长下滑"


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
