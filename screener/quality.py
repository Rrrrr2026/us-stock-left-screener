#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块9 — 👑 优质公司推荐 (Quality Compounders)
=============================================
与"左侧候选"不同: 从数据库累计覆盖过的所有股票 (逐日滚动扩大) 里找
"连续增长 + 持续超预期 + 行业龙头 + 高ROE + 深护城河 + 估值不贵"的公司,
每天打分排名前10; 硬性门槛全过的标记 👑, 未全过的列出差在哪一条。

数据: fundamental 表按 code 取最近一行 (近14天内) —— 单季EPS同比×4、
营收同比×4、ROE、PE-TTM、行业营收排名都在; 入围短名单再用 yfinance 逐只补:
年度利润表 (营收/净利年度连增 + 研发费用) 与财报日历 (实际EPS vs 一致预期,
最近4次全部超预期 = BEAT 门槛, 这是美股版独有的"超市场预期"实数据)。

硬性门槛 (全过 = 👑):
  Q4   近四个单季: EPS同比与营收同比全部 > 0
  Y4   年度营收与净利连增 (yfinance 年报深度约4年 -> 至少3个完整年度同比>0)
  BEAT 最近4次财报实际EPS全部 >= 一致预期
  ROE  >= 15%
  PE   0 < PE-TTM < 31
  DOM  行业营收排名 <= 3
加分: 研发强度 (研发费用/营收) / 增长加速 / 更低估值 / 更大份额。
榜单是"客观条件筛选"而非投资建议; 财报与估值有滞后, 买前仍需人工研究。
"""
from __future__ import annotations
import datetime as dt
import json
import logging
import os
import sqlite3

import numpy as np

from .config import DASHBOARD_DIR, DATA_DIR, DB_PATH

log = logging.getLogger("screener.quality")

QL_JS = os.path.join(DASHBOARD_DIR, "quality_data.js")
QL_JSON = os.path.join(DATA_DIR, "quality_result.json")

PE_MAX = 31.0
ROE_MIN = 15.0
DOM_RANK_MAX = 3
TOP_N = 10
SHORTLIST = 30
STALE_DAYS = 14          # fundamental 行超过该天数视为过期, 不参与
RD_GOOD, RD_OK = 5.0, 3.0
GATE_KEYS = ("q4", "y4", "beat", "roe", "pe", "dom")


def _loads(s, default=None):
    try:
        return json.loads(s) if s else default
    except Exception:
        return default


def _latest_fundamentals() -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cutoff = (dt.date.today() - dt.timedelta(days=STALE_DAYS)).isoformat()
    rows = conn.execute(
        "SELECT f.*, t.name AS name, t.industry AS industry FROM fundamental f "
        "JOIN (SELECT code, MAX(run_date) mr FROM fundamental GROUP BY code) m "
        "ON f.code = m.code AND f.run_date = m.mr "
        "LEFT JOIN tech_scan t ON t.code = f.code AND t.run_date = f.run_date "
        "WHERE f.run_date >= ?",
        (cutoff,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _enrich(code: str) -> dict:
    """短名单逐只: 年度连增 / 研发强度 / 最近4次财报超预期。失败字段置 None。"""
    out = {"y4": None, "y_yoy": None, "rd": None, "beat": None, "surprises": None}
    try:
        import yfinance as yf
        t = yf.Ticker(code)
        try:
            inc = t.income_stmt
            if inc is not None and len(inc.columns) >= 4:
                def _ser(*names):
                    for nm in names:
                        if nm in inc.index:
                            s = inc.loc[nm].dropna()
                            return [float(v) for v in s[::-1]]     # 旧→新
                    return None
                rev = _ser("Total Revenue", "Operating Revenue")
                ni = _ser("Net Income", "Net Income Common Stockholders")
                if rev and ni and len(rev) >= 4 and len(ni) >= 4:
                    ry = [(b - a) / abs(a) * 100 for a, b in zip(rev, rev[1:]) if a > 0]
                    ny = [(b - a) / abs(a) * 100 for a, b in zip(ni, ni[1:]) if a > 0]
                    if len(ry) >= 3 and len(ny) >= 3:
                        out["y4"] = bool(all(v > 0 for v in ry[-3:])
                                         and all(v > 0 for v in ny[-3:]))
                        out["y_yoy"] = [round(v, 1) for v in ny[-3:]]
                rd = _ser("Research And Development")
                if rd and rev and rev[-1] > 0:
                    out["rd"] = round(rd[-1] / rev[-1] * 100.0, 1)
        except Exception:
            pass
        try:
            ed = t.get_earnings_dates(limit=12)
            if ed is not None and "Surprise(%)" in ed.columns:
                sp = ed["Surprise(%)"].dropna()
                if len(sp) >= 4:
                    last4 = [float(v) for v in sp.iloc[:4]]
                    out["surprises"] = [round(v, 1) for v in last4]
                    out["beat"] = bool(all(v >= 0 for v in last4))
        except Exception:
            pass
    except Exception as e:
        log.debug("enrich %s 失败: %s", code, e)
    return out

def _long_hist(code: str):
    import yfinance as yf
    df = yf.Ticker(code).history(period="5y", auto_adjust=True)
    if df is None or len(df) < 120:
        return None
    return (df["High"].to_numpy(float), df["Close"].to_numpy(float))


def build_quality(top_n: int = TOP_N) -> dict | None:
    funds = _latest_fundamentals()
    if not funds:
        log.warning("fundamental 表为空, 优质榜跳过")
        return None
    rows = []
    for f in funds:
        ni_q4 = [v for v in (_loads(f.get("ni_qoq_json"), []) or []) if isinstance(v, (int, float))]
        rev_q4 = [v for v in (_loads(f.get("rev_qoq_json"), []) or []) if isinstance(v, (int, float))]
        roe = f.get("roe")
        pe = f.get("pe_ttm")
        dr = f.get("dom_rank")
        g_q4 = bool(len(ni_q4) >= 4 and len(rev_q4) >= 3
                    and all(v > 0 for v in ni_q4[-4:]) and all(v > 0 for v in rev_q4[-3:]))
        g_roe = bool(isinstance(roe, (int, float)) and roe >= ROE_MIN)
        g_pe = bool(isinstance(pe, (int, float)) and 0 < pe < PE_MAX)
        g_dom = bool(isinstance(dr, (int, float)) and dr <= DOM_RANK_MAX)
        n_pre = sum((g_q4, g_roe, g_pe, g_dom))
        if n_pre < 3:                    # 存量门槛先过3条才有资格进短名单
            continue
        score = 0.0
        if isinstance(roe, (int, float)):
            score += min(30.0, max(0.0, roe))
        if isinstance(pe, (int, float)) and pe > 0:
            score += 10.0 if pe < 20 else (5.0 if pe < PE_MAX else 0.0)
        if isinstance(dr, (int, float)):
            score += 15.0 if dr == 1 else (10.0 if dr <= 3 else 0.0)
        if g_q4:
            score += 10.0
        ttm = f.get("ni_ttm_yoy")
        accel = bool(ni_q4 and isinstance(ttm, (int, float)) and ni_q4[-1] >= ttm)
        if accel:
            score += 8.0
        rows.append({
            "code": f["code"], "name": f.get("name"), "industry": f.get("industry"),
            "run_date": f.get("run_date"),
            "pe": round(pe, 1) if isinstance(pe, (int, float)) else None,
            "roe": round(roe, 1) if isinstance(roe, (int, float)) else None,
            "dom_rank": dr,
            "dom_share": f.get("dom_share"),
            "ni_q4": [round(v, 1) for v in ni_q4[-4:]] if ni_q4 else None,
            "rev_q4": [round(v, 1) for v in rev_q4[-4:]] if rev_q4 else None,
            "accel": accel,
            "gates": {"q4": g_q4, "roe": g_roe, "pe": g_pe, "dom": g_dom},
            "score": round(score, 1),
        })
    rows.sort(key=lambda r: (-sum(r["gates"].values()), -(r["score"] or 0)))
    short = rows[:SHORTLIST]
    for r in short:
        e = _enrich(r["code"])
        r["ni_y4"] = e["y_yoy"]
        r["rd"] = e["rd"]
        r["surprises"] = e["surprises"]
        r["gates"]["y4"] = bool(e["y4"])
        r["gates"]["beat"] = bool(e["beat"])
        if e["rd"] is not None:
            r["score"] = round(r["score"] + (10.0 if e["rd"] >= RD_GOOD
                                             else (6.0 if e["rd"] >= RD_OK else 0.0)), 1)
        if e["y4"]:
            r["score"] = round(r["score"] + 10.0, 1)
        if e["beat"]:
            r["score"] = round(r["score"] + 10.0, 1)
        r["n_pass"] = sum(bool(r["gates"].get(k)) for k in GATE_KEYS)
    short.sort(key=lambda r: (-(r.get("n_pass") or 0), -(r["score"] or 0)))
    picks = short[:top_n]
    try:
        from . import prob20
        prob20.annotate(picks, _long_hist, conditional=False, key="p20")
    except Exception as e:
        log.warning("30日涨20%%概率计算失败: %s", e)
    n_crown = sum(1 for r in picks if r.get("n_pass") == len(GATE_KEYS))
    result = {
        "meta": {"date": dt.date.today().isoformat(),
                 "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                 "n_screened": len(funds), "n_pool": len(rows),
                 "n_crown": n_crown, "pe_max": PE_MAX, "roe_min": ROE_MIN},
        "picks": picks,
    }
    json.dump(result, open(QL_JSON, "w", encoding="utf-8"), ensure_ascii=False)
    with open(QL_JS, "w", encoding="utf-8") as f:
        f.write("window.__QL__ = ")
        json.dump(result, f, ensure_ascii=False)
        f.write(";\n")
    log.info("优质榜: 覆盖 %d, 入池 %d, 榜单 %d (👑全过 %d)",
             len(funds), len(rows), len(picks), n_crown)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    build_quality()
