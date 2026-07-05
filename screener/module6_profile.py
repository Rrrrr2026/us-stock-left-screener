#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块6 — 个股深度档案 (Company Profile)
=======================================
仅对最终候选执行 (阶段C), 组装:
  公司简介/管理层/员工数 · 营收增速与成本结构 · 现金流分析(+自动"漏洞"要点)
  治理与做空等风险指标 · 新闻(利好/利空粗分) · 期权博弈概览 · FINRA场外空头占比
全部字段缺失安全: 取不到 -> None/[] , 前端显示"暂无"。
"""
from __future__ import annotations
import logging
import numpy as np

from . import datasource as ds

log = logging.getLogger("screener.module6")


def _num(x):
    if x is None:
        return None
    try:
        v = float(x)
        return None if (np.isnan(v) or np.isinf(v)) else v
    except Exception:
        return None


def _officers(info: dict, top: int = 5) -> list:
    out = []
    for o in (info.get("companyOfficers") or [])[:top]:
        if not isinstance(o, dict) or not o.get("name"):
            continue
        pay = o.get("totalPay")
        if isinstance(pay, dict):
            pay = pay.get("raw")
        pay = _num(pay)
        out.append({"name": o.get("name"), "title": o.get("title") or "—",
                    "age": o.get("age"),
                    "pay_m": round(pay / 1e6, 2) if pay else None})   # 年薪(百万美元)
    return out


def _last(lst):
    """取列表最后一个非 None 值及其下标。"""
    if not lst:
        return None, None
    for i in range(len(lst) - 1, -1, -1):
        if lst[i] is not None:
            return lst[i], i
    return None, None


def _cash_insights(cf: dict) -> list:
    """规则化生成"钱流向/漏洞"中文要点。level: good/warn/info"""
    notes = []
    if not cf or not cf.get("years"):
        return notes
    years = cf["years"]

    def v(k, i):
        arr = cf.get(k) or []
        return arr[i] if (i is not None and i < len(arr)) else None

    ocf, i = _last(cf.get("ocf") or [])
    yr = years[i] if i is not None else years[-1]
    ni = v("net_income", i)
    fcf = v("fcf", i)
    capex = v("capex", i)
    acq = v("acq", i)
    buyback = v("buyback", i)
    dividend = v("dividend", i)
    debt_net = v("debt_net", i)
    if debt_net is None:
        di, dr = v("debt_iss", i), v("debt_rep", i)
        if di is not None or dr is not None:
            debt_net = (di or 0) + (dr or 0)   # 偿还为负值

    def add(level, zh, en):
        notes.append({"level": level, "text": zh, "text_en": en})

    # 1) 盈利质量: 经营现金流 vs 净利润
    if ocf is not None and ni is not None and ni > 0:
        r = ocf / ni
        if r < 0.7:
            add("warn",
                f"{yr}年经营现金流仅为净利润的{r*100:.0f}% —— 利润未充分转化为现金(应收/存货占款?), 盈利质量需警惕",
                f"FY{yr} operating cash flow is only {r*100:.0f}% of net income — profit isn't converting to cash (receivables/inventory tie-up?); earnings quality is a concern")
        elif r > 1.1:
            add("good",
                f"{yr}年经营现金流为净利润的{r*100:.0f}% —— 利润含金量高",
                f"FY{yr} operating cash flow is {r*100:.0f}% of net income — high-quality earnings")
    if ni is not None and ni < 0 and ocf is not None and ocf > 0:
        add("info",
            f"{yr}年账面亏损但经营现金流为正(${ocf:,.0f}M) —— 亏损或主要来自非现金项目(摊销/减值)",
            f"FY{yr} shows a book loss but positive operating cash flow (${ocf:,.0f}M) — the loss likely stems mainly from non-cash items (amortization/impairment)")

    # 2) 自由现金流
    if fcf is not None and fcf < 0:
        add("warn",
            f"{yr}年自由现金流为负(${fcf:,.0f}M) —— 经营造血不足以覆盖资本开支, 需外部融资",
            f"FY{yr} free cash flow is negative (${fcf:,.0f}M) — operations don't cover capex; external financing needed")

    # 3) 大额收购
    if acq is not None and acq < 0 and ocf and ocf > 0 and abs(acq) >= 0.3 * ocf:
        add("warn",
            f"{yr}年收购支出${abs(acq):,.0f}M, 相当于经营现金流的{abs(acq)/ocf*100:.0f}% —— 关注商誉与整合风险",
            f"FY{yr} spent ${abs(acq):,.0f}M on acquisitions, ~{abs(acq)/ocf*100:.0f}% of operating cash flow — watch goodwill and integration risk")

    # 4) 股东回馈 vs 造血能力
    ret = abs(buyback or 0) + abs(dividend or 0)
    if ret > 0 and fcf is not None:
        if fcf > 0 and ret > fcf and (debt_net or 0) > 0:
            add("warn",
                f"{yr}年回购+分红(${ret:,.0f}M)超过自由现金流(${fcf:,.0f}M)且当年净举债 —— 借钱回馈股东, 不可持续",
                f"FY{yr} buybacks+dividends (${ret:,.0f}M) exceed free cash flow (${fcf:,.0f}M) with net new borrowing — funding shareholder returns with debt, unsustainable")
        elif fcf > 0 and ret > 0.9 * fcf:
            add("info",
                f"{yr}年回购+分红(${ret:,.0f}M)几乎用尽自由现金流 —— 留给扩张/还债的余地小",
                f"FY{yr} buybacks+dividends (${ret:,.0f}M) nearly exhaust free cash flow — little left for expansion/debt paydown")
        elif fcf > 0:
            add("good",
                f"{yr}年以${ret:,.0f}M回馈股东(回购${abs(buyback or 0):,.0f}M+分红${abs(dividend or 0):,.0f}M), 自由现金流覆盖充分",
                f"FY{yr} returned ${ret:,.0f}M to shareholders (buybacks ${abs(buyback or 0):,.0f}M + dividends ${abs(dividend or 0):,.0f}M), well covered by free cash flow")

    # 5) 资本开支强度趋势
    capex_arr = [x for x in (cf.get("capex") or []) if x is not None]
    if len(capex_arr) >= 3 and capex is not None and ocf and ocf > 0:
        if abs(capex) > 0.8 * ocf:
            add("info",
                f"{yr}年资本开支${abs(capex):,.0f}M, 占经营现金流的{abs(capex)/ocf*100:.0f}% —— 重资产扩张期, 关注回报率",
                f"FY{yr} capex ${abs(capex):,.0f}M is {abs(capex)/ocf*100:.0f}% of operating cash flow — capital-intensive expansion phase; watch returns")
    return notes


def pull_profile(code: str, sector: str | None = None,
                 short_map: dict | None = None) -> dict:
    p = {
        "summary": None, "website": None, "hq": None, "employees": None,
        "officers": [], "governance": None,
        "short": None, "revenue": None, "cashflow": None, "cash_notes": [],
        "news": [], "options": None, "darkpool": None, "sector": sector,
    }
    info = ds.fetch_info(code) or {}

    # ---- 简介 / 管理层 / 员工 ----
    p["summary"] = info.get("longBusinessSummary")
    p["website"] = info.get("website")
    city, state = info.get("city"), info.get("state")
    p["hq"] = ", ".join(x for x in (city, state, info.get("country")) if x) or None
    p["employees"] = info.get("fullTimeEmployees")
    p["officers"] = _officers(info)

    # ---- 治理/风险指标 (ISS 1-10, 10=风险最高) + 做空 + 偿债 ----
    p["governance"] = {
        "audit": info.get("auditRisk"), "board": info.get("boardRisk"),
        "compensation": info.get("compensationRisk"),
        "shareholder": info.get("shareHolderRightsRisk"),
        "overall": info.get("overallRisk"),
    }
    # yfinance 的 shortPercentOfFloat 是小数(1.05=流通盘的105%, 逼空股会>1);
    # 仅当离谱地大(>5, 即500%)才视为已是百分数
    spf = _num(info.get("shortPercentOfFloat"))
    if spf is not None:
        spf = round(spf * 100.0, 2) if spf <= 5 else round(spf, 2)
    p["short"] = {
        "pct_float": spf,
        "days_to_cover": _num(info.get("shortRatio")),
        "inst_held_pct": round(_num(info.get("heldPercentInstitutions")) * 100.0, 1)
        if _num(info.get("heldPercentInstitutions")) is not None else None,
        "current_ratio": _num(info.get("currentRatio")),
        "debt_to_equity": _num(info.get("debtToEquity")),
    }

    # ---- 营收 / 现金流 / 新闻 / 期权 ----
    p["revenue"] = ds.fetch_revenue_trend(code) or None
    cf = ds.fetch_cashflow(code) or None
    p["cashflow"] = cf
    p["cash_notes"] = _cash_insights(cf or {})
    p["news"] = ds.fetch_news(code)
    p["options"] = ds.fetch_options_summary(code) or None

    # ---- FINRA 场外(含暗池)空头成交占比 ----
    # FINRA 对双类股用斜杠 (BRK/B), 股票池用点 (BRK.B) — 两种写法都查
    if short_map:
        p["darkpool"] = short_map.get(code) or short_map.get(code.replace(".", "/"))
    return p
