#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""strategy_beat — 「目标价全线在上」加严筛选层 + 前瞻重放回测。

筛选条件 (用户定义, 2026-08-31):
  1. 分析师 >= 25 人           2. 现价到 Min 目标 >= 10%      3. 现价到 Avg 目标 >= 20%
  4. 营收超预期 (v1=最新一季, 逐日存档滚满4季后升级为连续4季)
  5. EPS 连续 4 个季度超预期 (yfinance earnings_history, 恰好4季)
交易规则:
  买入 = 全条件满足日的次日收盘 (防同日前视); 获利卖出 = 盘中触及买入日锁定的 Avg
  (按 Avg 限价成交); 风控卖出 = 当日快照 Avg < 买入日 Avg × 0.98, 按收盘卖。
  往返成本 0.2%。无固定止损 (用户规则如此 — 敞口风险自负)。
数据现实 (诚实声明): 分析师目标价没有免费历史序列 — 回测采用「逐日快照留痕 →
前瞻重放」(与主系统信号回测同方法论), 从 2026-08-30 起每天真实积累, 无法向过去伪造。

用法:  python strategy_beat.py screen   # 今日筛选 + 写当日信号档 data/beat/
       python strategy_beat.py replay   # 基于全部存档快照重放策略 (需 yfinance 拉价)
"""
import datetime
import glob
import json
import pathlib
import sys
import time

BASE = pathlib.Path(__file__).parent
SNAP_DIR = BASE / "data" / "history_snap"
BEAT_DIR = BASE / "data" / "beat"
SCREEN = BASE / "data" / "screen.json"

MIN_ANALYSTS = 25
MIN_UP_MIN = 0.10
MIN_UP_AVG = 0.20
AVG_CUT = 0.98          # 风控: 现avg < 入场avg × 0.98 即卖
COST_RT = 0.002

SCAN_URL = "https://scanner.tradingview.com/america/scan"
HEADERS = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0",
           "Origin": "https://www.tradingview.com", "Referer": "https://www.tradingview.com/"}


def _load_screen(path=SCREEN):
    j = json.load(open(path, encoding="utf-8"))
    return j


def _base_filter(j):
    """条件1-3。行序见 CLAUDE.md schema。"""
    out = []
    for r in j["rows"]:
        sym, name, _, close, tlow, tavg = r[0], r[1], r[2], r[3], r[4], r[5]
        ntotal = r[9]
        if not close or close <= 0:
            continue
        up_min, up_avg = tlow / close - 1.0, tavg / close - 1.0
        if ntotal >= MIN_ANALYSTS and up_min >= MIN_UP_MIN and up_avg >= MIN_UP_AVG:
            out.append({"sym": sym, "name": name, "close": close, "tlow": tlow,
                        "tavg": tavg, "n": ntotal,
                        "up_min": round(up_min * 100, 1), "up_avg": round(up_avg * 100, 1)})
    return out


def _tv_extra(symbols):
    """批量取最新一季 EPS/营收 实际vs预期 (TV scanner, 一次请求)。"""
    import requests
    cols = ["name", "earnings_per_share_fq", "earnings_per_share_forecast_fq",
            "revenue_fq", "revenue_forecast_fq", "eps_surprise_percent_fq"]
    body = {"symbols": {"tickers": symbols}, "columns": cols}
    r = requests.post(SCAN_URL, json=body, headers=HEADERS, timeout=30)
    r.raise_for_status()
    out = {}
    for row in r.json().get("data") or []:
        d = row["d"]
        out[row["s"]] = {"eps_a": d[1], "eps_f": d[2], "rev_a": d[3], "rev_f": d[4],
                         "eps_sup_lq": d[5]}
    return out


def _eps_4q_beats(ticker):
    """yfinance earnings_history: 近4季 EPS 是否全部超预期。返回 (bool, [surprise%×4])。"""
    import yfinance as yf
    try:
        h = yf.Ticker(ticker).earnings_history
        if h is None or len(h) < 4:
            return None, []
        h = h.tail(4)
        sup = []
        for _, row in h.iterrows():
            a, e = row.get("epsActual"), row.get("epsEstimate")
            if a is None or e is None or a != a or e != e:
                return None, []
            sup.append(round(float(a) - float(e), 4))
        return all(s > 0 for s in sup), sup
    except Exception:
        return None, []


def cmd_screen():
    j = _load_screen()
    day = (j.get("fetchedAt") or "")[:10] or datetime.date.today().isoformat()
    base = _base_filter(j)
    print(f"[{day}] 基础池 {len(j['rows'])} 只 → 条件1-3 (n>=25, min+10%, avg+20%): {len(base)} 只")
    if not base:
        return _write_beat(day, [])
    extra = _tv_extra([b["sym"] for b in base])
    rev_ok = []
    for b in base:
        e = extra.get(b["sym"]) or {}
        ra, rf = e.get("rev_a"), e.get("rev_f")
        b["rev_beat_lq"] = bool(ra and rf and ra > rf)
        b["eps_sup_lq"] = e.get("eps_sup_lq")
        if b["rev_beat_lq"]:
            rev_ok.append(b)
    print(f"→ 条件4 (最新一季营收超预期): {len(rev_ok)} 只")
    final = []
    for i, b in enumerate(rev_ok, 1):
        tk = b["sym"].split(":")[-1]
        ok, sup = _eps_4q_beats(tk)
        b["eps_beats_4q"] = ok
        b["eps_sup_4q"] = sup
        if ok:
            final.append(b)
        time.sleep(0.35)
        if i % 20 == 0:
            print(f"   EPS四季核查 {i}/{len(rev_ok)} ...")
    print(f"→ 条件5 (EPS 连续4季超预期): {len(final)} 只\n")
    final.sort(key=lambda x: -x["up_avg"])
    for b in final:
        print(f"  {b['sym']:<18} {b['name'][:24]:<26} 收{b['close']:>9.2f}  "
              f"到Min +{b['up_min']:>5.1f}%  到Avg +{b['up_avg']:>5.1f}%  "
              f"分析师{b['n']:>3}  EPS超幅{b['eps_sup_4q']}")
    _write_beat(day, final)
    return final


def _write_beat(day, final):
    BEAT_DIR.mkdir(parents=True, exist_ok=True)
    path = BEAT_DIR / f"beat_{day}.json"
    json.dump({"date": day, "criteria": {"min_analysts": MIN_ANALYSTS,
               "up_min": MIN_UP_MIN, "up_avg": MIN_UP_AVG,
               "rev_scope": "latest_q(archiving_to_4q)", "eps_scope": "4q_yf"},
               "names": final}, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"\n信号档 → {path}  ({len(final)} 只)")


def cmd_replay():
    snaps = {}
    for p in sorted(glob.glob(str(SNAP_DIR / "screen_*.json"))):
        day = pathlib.Path(p).stem.replace("screen_", "")
        j = json.load(open(p, encoding="utf-8"))
        snaps[day] = {r[0]: {"close": r[3], "tavg": r[5]} for r in j["rows"]}
    beats = {}
    for p in sorted(glob.glob(str(BEAT_DIR / "beat_*.json"))):
        j = json.load(open(p, encoding="utf-8"))
        beats[j["date"]] = [b["sym"] for b in j["names"]]
    days = sorted(snaps)
    print(f"重放素材: 快照 {len(days)} 天 ({days[0] if days else '-'} → {days[-1] if days else '-'}), "
          f"信号日 {len(beats)} 天")
    if len(days) < 2:
        print("快照不足两天 — 重放从明天起逐日有效 (机制已就绪)")
        return
    # 价格与盘中高点: 统一用 yfinance (持仓可能离开筛选宇宙, 快照价不够用)
    import yfinance as yf
    held_syms = sorted({s for b in beats.values() for s in b})
    if not held_syms:
        print("尚无任何信号 — 无交易可重放")
        return
    tickers = [s.split(":")[-1] for s in held_syms]
    px = yf.download(tickers, start=days[0], progress=False, auto_adjust=True,
                     group_by="ticker", threads=True)
    def bar(tk, day):
        try:
            if len(tickers) == 1:
                row = px.loc[day]
            else:
                row = px[tk].loc[day]
            return float(row["Close"]), float(row["High"])
        except Exception:
            return None, None
    open_pos, closed = {}, []
    for i, day in enumerate(days):
        # 先出场
        for sym in list(open_pos):
            pos = open_pos[sym]
            tk = sym.split(":")[-1]
            c, h = bar(tk, day)
            if c is None:
                continue
            cur = snaps[day].get(sym)
            if h is not None and h >= pos["tgt"]:                     # 触及锁定avg: 限价成交
                closed.append({**pos, "exit_day": day, "exit_px": pos["tgt"],
                               "why": "target",
                               "ret": pos["tgt"] / pos["entry_px"] - 1 - COST_RT})
                del open_pos[sym]
            elif cur and cur["tavg"] < pos["entry_avg"] * AVG_CUT:    # 风控: avg 下调>=2%
                closed.append({**pos, "exit_day": day, "exit_px": c, "why": "avg_cut",
                               "ret": c / pos["entry_px"] - 1 - COST_RT})
                del open_pos[sym]
        # 再进场: 前一快照日的信号 → 今日收盘买入
        if i > 0:
            for sym in beats.get(days[i - 1], []):
                if sym in open_pos:
                    continue
                tk = sym.split(":")[-1]
                c, _ = bar(tk, day)
                prev = snaps[days[i - 1]].get(sym)
                if c and prev:
                    open_pos[sym] = {"sym": sym, "entry_day": day, "entry_px": c,
                                     "entry_avg": prev["tavg"], "tgt": prev["tavg"]}
    print(f"\n已了结 {len(closed)} 笔:")
    for t in closed:
        print(f"  {t['sym']:<16} {t['entry_day']}→{t['exit_day']} {t['why']:<7} "
              f"{t['ret']*100:+.2f}%")
    if closed:
        rets = [t["ret"] for t in closed]
        print(f"  合计: 胜率 {sum(1 for r in rets if r > 0)/len(rets):.0%} · "
              f"平均 {sum(rets)/len(rets)*100:+.2f}%/笔 (含成本)")
    print(f"\n持仓中 {len(open_pos)} 笔:")
    for p in open_pos.values():
        print(f"  {p['sym']:<16} {p['entry_day']} 入 {p['entry_px']:.2f} → 目标 {p['tgt']:.2f} "
              f"(+{(p['tgt']/p['entry_px']-1)*100:.1f}%)")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "screen"
    (cmd_screen if cmd == "screen" else cmd_replay)()
