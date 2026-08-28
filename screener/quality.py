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
GATE_KEYS = ("q4", "y4", "beat", "roe", "pe", "dom", "cap", "up")
CAP_MIN = 10e9           # 蓝筹门槛: 市值 >= $10B
UPSIDE_MIN = 20.0        # 盈利空间门槛: 分析师目标价上行 >= 20%


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
    # 蓝筹市值映射 (全股票池, NASDAQ 名单自带市值)
    mcap_map = {}
    try:
        from . import datasource as _ds
        _u = _ds.get_universe()
        if _u is not None and "mcap" in _u.columns:
            mcap_map = {str(r["code"]): float(r["mcap"]) for _, r in _u.iterrows() if r["mcap"] and r["mcap"] > 0}
    except Exception as e:
        log.warning("市值映射失败(蓝筹门槛降级): %s", e)
    # 并入 qfund (轮转抓取的全股票池基本面): 候选之外的股票也能进优质榜
    try:
        from . import qfund
        have = {f["code"] for f in funds}
        dmap = qfund.dom_rank_map()
        n_add = 0
        for r in qfund.load_all():
            if r["code"] in have:
                continue
            dr = dmap.get(r["code"])
            funds.append({
                "code": r["code"], "name": r.get("name"), "industry": r.get("sector"),
                "run_date": r.get("asof"), "ni_qoq_json": r.get("eps_q4_json"),
                "rev_qoq_json": json.dumps([r["rev_yoy"]] if r.get("rev_yoy") is not None else []),
                "roe": r.get("roe"), "pe_ttm": r.get("pe"), "ni_ttm_yoy": None,
                "upside_pct": r.get("upside"), "mcap": r.get("mcap"),
                "dom_rank": dr[0] if dr else None, "dom_share": dr[1] if dr else None,
                "src": "qfund",
            })
            n_add += 1
        log.info("优质榜: 候选基本面 %d + qfund 广度 %d", len(have), n_add)
    except Exception as e:
        log.warning("qfund 合并失败(仅用候选基本面): %s", e)
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
        # 营收: 候选有3-4个单季同比; qfund 广度行只有最新季同比 -> 有多少看多少, 全正才过
        g_q4 = bool(len(ni_q4) >= 4 and len(rev_q4) >= 1
                    and all(v > 0 for v in ni_q4[-4:]) and all(v > 0 for v in rev_q4[-3:]))
        g_roe = bool(isinstance(roe, (int, float)) and roe >= ROE_MIN)
        g_pe = bool(isinstance(pe, (int, float)) and 0 < pe < PE_MAX)
        g_dom = bool(isinstance(dr, (int, float)) and dr <= DOM_RANK_MAX)
        mcap = mcap_map.get(str(f["code"])) or (f.get("mcap") if isinstance(f.get("mcap"), (int, float)) else None)
        g_cap = bool(mcap is not None and mcap >= CAP_MIN)
        upside = f.get("upside_pct") if isinstance(f.get("upside_pct"), (int, float)) else None
        g_up = bool(upside is not None and upside >= UPSIDE_MIN)
        n_pre = sum((g_q4, g_roe, g_pe, g_dom, g_cap, g_up))
        if n_pre < 4:                    # 8 个门槛更严: 存量门槛先过 4 条才有资格进短名单
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
        if upside is not None:
            score += min(15.0, max(0.0, upside) / 4.0)      # 盈利空间加分 (60%+ 拉满)
        if g_cap:
            score += 5.0
        rows.append({
            "code": f["code"], "name": f.get("name"), "industry": f.get("industry"),
            "run_date": f.get("run_date"),
            "pe": round(pe, 1) if isinstance(pe, (int, float)) else None,
            "roe": round(roe, 1) if isinstance(roe, (int, float)) else None,
            "dom_rank": dr,
            "dom_share": f.get("dom_share"),
            "mcap_b": round(mcap / 1e9, 1) if mcap else None,
            "val_model": ("分析师目标" if upside is not None else None),
            "upside": round(upside, 1) if upside is not None else None,
            "ni_q4": [round(v, 1) for v in ni_q4[-4:]] if ni_q4 else None,
            "rev_q4": [round(v, 1) for v in rev_q4[-4:]] if rev_q4 else None,
            "accel": accel,
            "gates": {"q4": g_q4, "roe": g_roe, "pe": g_pe, "dom": g_dom, "cap": g_cap, "up": g_up},
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
    # 每日榜单落盘到 history/, 供"榜单战绩"回测 (优质榜不在候选快照里, 需自己留痕)
    try:
        hdir = os.path.join(DASHBOARD_DIR, "history")
        os.makedirs(hdir, exist_ok=True)
        slim = [{k: p.get(k) for k in ("code", "name", "industry", "score", "n_pass", "pe", "roe", "gates")}
                for p in picks]
        with open(os.path.join(hdir, f"quality_{result['meta']['date']}.json"), "w", encoding="utf-8") as f:
            json.dump({"date": result["meta"]["date"], "picks": slim}, f, ensure_ascii=False)
    except Exception as e:
        log.warning("优质榜历史落盘失败: %s", e)
    try:
        result["deep_profiles"] = _deep_profiles(picks)
    except Exception as e:
        log.warning("优质深度档案失败(不影响榜单): %s", e)
    try:
        result["profiles"] = _drawer_profiles(picks)
    except Exception as e:
        log.warning("优质榜弹窗档案失败(不影响榜单): %s", e)
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


def _drawer_profiles(picks: list) -> dict:
    """优质榜前10只生成候选股同构档案 (yfinance 行情+info, 尽量填满总览页字段)。"""
    import glob
    import pandas as pd
    from leftside_core import indicators as ind
    template = {}
    days = sorted(glob.glob(os.path.join(DASHBOARD_DIR, "history", "day_*.json")))
    if days:
        try:
            cands = (json.load(open(days[-1], encoding="utf-8")).get("candidates") or [])
            if cands:
                template = {k: None for k in cands[0]}
        except Exception:
            pass
    codes = [p["code"] for p in picks if p.get("code")]
    hists, infos, roes = {}, {}, {}
    try:
        import yfinance as yf
        df = yf.download(codes, period="1y", auto_adjust=True, progress=False,
                         group_by="ticker", threads=True)
        for c in codes:
            try:
                sub = df[c] if isinstance(df.columns, pd.MultiIndex) else df
                sub = sub.dropna(subset=["Close"])
                if len(sub) >= 60:
                    hists[c] = sub
            except Exception:
                continue
        for c in codes:
            try:
                t = yf.Ticker(c)
                infos[c] = t.info or {}
                try:
                    inc, bal = t.income_stmt, t.balance_sheet
                    ni = inc.loc["Net Income"] if inc is not None and "Net Income" in inc.index else None
                    eq = None
                    if bal is not None:
                        for nm in ("Stockholders Equity", "Total Equity Gross Minority Interest"):
                            if nm in bal.index:
                                eq = bal.loc[nm]
                                break
                    if ni is not None and eq is not None:
                        pts = []
                        for col in ni.index:
                            n, e = ni.get(col), eq.get(col)
                            if n == n and e and e == e and e > 0:
                                pts.append({"date": str(col)[:10], "value": round(float(n) / float(e) * 100, 1)})
                        pts.sort(key=lambda x: x["date"])
                        if pts:
                            roes[c] = pts
                except Exception:
                    pass
            except Exception:
                infos[c] = {}
    except Exception as e:
        log.warning("优质档案行情失败: %s", e)

    def _n(v, scale=1.0, nd=2):
        try:
            v = float(v)
            return round(v * scale, nd) if v == v else None
        except (TypeError, ValueError):
            return None

    out = {}
    for i, p in enumerate(picks, 1):
        code = p.get("code")
        sub = hists.get(code)
        if sub is None:
            continue
        try:
            close, high, low, vol = sub["Close"], sub["High"], sub["Low"], sub.get("Volume")
            k, d, jv = ind.kdj(high, low, close)
            k, d, jv = (float(k.iloc[-1]), float(d.iloc[-1]), float(jv.iloc[-1]))
            price = float(close.iloc[-1])
            h52, l52 = float(high.max()), float(low.min())
            info = infos.get(code) or {}
            vr = sig_vol = None
            if vol is not None and len(vol) >= 20 and float(vol.iloc[-20:].mean()) > 0:
                vr = round(float(vol.iloc[-5:].mean()) / float(vol.iloc[-20:].mean()), 2)
                sig_vol = "缩量" if vr < 0.7 else ("放量" if vr > 1.5 else "平量")
            dy = _n(info.get("dividendYield"), 1.0, 4)
            if dy is not None and dy < 0.02:         # 旧版yfinance给小数比例, 新版直接给百分数
                dy = round(dy * 100, 2)
            prof = dict(template)
            prof.update({
                "code": code, "name": p.get("name"), "industry": p.get("industry"),
                "tag": "🔎 观察", "price": round(price, 2),
                "spark": [round(float(v), 2) for v in close.iloc[-40:]],
                "high_52w": round(h52, 2), "low_52w": round(l52, 2),
                "pos_52w_pct": round((price - l52) / (h52 - l52) * 100, 1) if h52 > l52 else None,
                "max_dd_pct": round(float(ind.max_drawdown(close)), 1),
                "atr_pct": round(float(ind.atr_pct(high, low, close)), 2),
                "boll_low": round(float(ind.bollinger_lower(close).iloc[-1]), 2),
                "vol_ratio_calc": vr, "sig_vol": sig_vol,
                "kdj_k": round(k, 1), "kdj_d": round(d, 1), "kdj_j": round(jv, 1),
                "kdj_tag": ind.kdj_tag(k, d, jv),
                "rsi": round(float(ind.rsi(close).iloc[-1]), 1),
                "pe_ttm": p.get("pe"), "pe_disp": (str(p.get("pe")) if p.get("pe") is not None else None),
                "pb": _n(info.get("priceToBook")),
                "eps": _n(info.get("trailingEps")),
                "roe": p.get("roe"),
                "revenue_yoy": _n(info.get("revenueGrowth"), 100.0, 1),
                "netprofit_yoy": _n(info.get("earningsGrowth"), 100.0, 1),
                "gross_margin": _n(info.get("grossMargins"), 100.0, 1),
                "debt_ratio": _n(info.get("debtToEquity")),
                "dividend_yield": dy,
                "beta": _n(info.get("beta")),
                "target_price": _n(info.get("targetMeanPrice")),
                "analyst_rating": info.get("recommendationKey"),
                "analyst_count": info.get("numberOfAnalystOpinions"),
                "upside_pct": p.get("upside"),
                "ni_qoq": p.get("ni_q4"), "roe_trend": roes.get(code),
                "fund_score": round(float(p.get("score") or 0)),
                "dom_rank": p.get("dom_rank"), "dom_share": p.get("dom_share"),
                "conclusion": f"👑 优质榜第{i}名 · 硬门槛 {p.get('n_pass')}/8 · 长线研究池标的, 非左侧信号; 买卖点/胜率仅候选股提供。",
                "conclusion_en": f"Quality #{i} · gates {p.get('n_pass')}/8 · research-pool name, not a left-side signal.",
            })
            out[code] = prof
        except Exception as e:
            log.debug("quality profile %s failed: %s", code, e)
    log.info("优质榜弹窗档案: %d/%d", len(out), len(picks))
    return out


def _deep_profiles(picks: list, budget_sec: int = 480) -> dict:
    """优质榜标的深度档案 (公司简介/主营/现金流/风险/消息): 库里最近的直接复用,
    没有或超过7天的在预算内现场补拉并入库 -> 弹窗四个页签不再空白。"""
    import sqlite3
    import time
    from .config import DB_PATH
    from . import module6_profile as m6
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    out = {}
    t0 = time.time()
    today = dt.date.today()
    for p in picks:
        code = p.get("code")
        prof, age = None, 999
        try:
            row = conn.execute(
                "SELECT run_date, profile_json FROM profile WHERE code=? "
                "ORDER BY run_date DESC LIMIT 1", (code,)).fetchone()
            if row and row["profile_json"]:
                prof = json.loads(row["profile_json"])
                age = (today - dt.date.fromisoformat(str(row["run_date"])[:10])).days
        except Exception:
            prof = None
        if (prof is None or age > 7) and time.time() - t0 < budget_sec:
            try:
                fresh = m6.pull_profile(code, sector=p.get("industry"))
                if fresh and (fresh.get("summary") or fresh.get("revenue") or fresh.get("cashflow")):
                    prof = fresh
                    conn.execute("INSERT OR REPLACE INTO profile(run_date,code,profile_json) "
                                 "VALUES(?,?,?)",
                                 (today.isoformat(), code, json.dumps(fresh, ensure_ascii=False)))
                    conn.commit()
            except Exception as e:
                log.debug("优质深度档案 %s: %s", code, e)
        if prof:
            out[code] = prof
    conn.close()
    log.info("优质榜深度档案: %d/%d", len(out), len(picks))
    return out
