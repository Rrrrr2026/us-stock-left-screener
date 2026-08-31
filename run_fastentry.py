# -*- coding: utf-8 -*-
"""⚡快弹 入场确认研究: 回弹确认 / 连串(cluster)逆向选择诊断 / 大盘急跌门 / 组合复验。

用户问题 (2026-09-01): 暴跌中连串快弹信号互相干扰(占满仓位槽的都是下跌前段的刀),
加"回弹信号/底部信号"指引是否更好?
方法: 事件集一次生成(检测与冷却与生产代理完全一致), 每个事件同时算三种入场:
  base    信号次日开盘 (现行)
  hi      等收盘突破前日最高(≤5日), 触发次日开盘进 — 经典回弹确认
  up2     等单日涨幅>=+2%(≤5日), 触发次日开盘进
无触发的信号记 skip 并测算"若按base进会怎样"(验证确认的价值=跳过了继续下跌的刀)。
止损距一律按信号日ATR定 (与生产一致, 保证跨入场模式可比)。
另: 连串深度 = 信号日前5个交易日全市场快弹信号数; 大盘门 = SPY 5日收益 >= -4%。
幸存者声明照旧。
"""
import json
import logging
import os
import sys
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import numpy as np                                      # noqa: E402

import run_fastgrid as fg                               # noqa: E402  (包装stdout, 勿重复包装)
import run_fastport as fp                               # noqa: E402
from leftside_core import exitgrid as xg                # noqa: E402
from leftside_core import pricestore as ps              # noqa: E402
from numpy.lib.stride_tricks import sliding_window_view  # noqa: E402

log = logging.getLogger("fastentry")

CUT3, CUT6 = "2023-09-01", "2020-09-01"
VAL_LO, VAL_HI = "2020-09-01", "2023-09-01"
PROD = "s18f5|t10|w20"
BEST = "s25f7|tR|w20"
CONFIRM_WAIT = 5
GATE_5D = -0.04


def scan_variants():
    m = xg.current()
    min_turn = xg.MIN_TURNOVER.get(m.name, 5e6)
    cost = m.cost_rt
    data = ps.load(sorted(ps.last_dates()))
    log.info("fastentry: %d 只票", len(data))
    reg = xg._regime_map()
    reg_dates = np.array(reg[0]) if reg is not None else None
    eps = []
    for ci, (code, ser) in enumerate(data.items(), 1):
        dates, ohlcv = ser["dates"], ser["ohlcv"]
        n = len(dates)
        if n < 320:
            continue
        o, h, l, c, v = (ohlcv[:, 0], ohlcv[:, 1], ohlcv[:, 2], ohlcv[:, 3], ohlcv[:, 4])
        ma, rsi14, atr = xg._ind(o, h, l, c)
        roll_hi = np.full(n, np.nan)
        roll_lo = np.full(n, np.nan)
        if n >= 250:
            roll_hi[249:] = sliding_window_view(h, 250).max(axis=1)
            roll_lo[249:] = sliding_window_view(l, 250).min(axis=1)
        next_ok = 300
        t = 300
        while t < n - 2:
            if (np.isnan(rsi14[t]) or np.isnan(atr[t]) or np.isnan(roll_hi[t])
                    or c[t] <= 0):
                t += xg.STRIDE
                continue
            if float(np.mean(v[t - 19:t + 1] * c[t - 19:t + 1])) < min_turn:
                t += xg.STRIDE
                continue
            if (t >= next_ok
                    and c[t] <= roll_hi[t] * (1.0 - xg.DIP_DD_MIN)
                    and rsi14[t] <= xg.DIP_RSI_MAX
                    and not np.isnan(roll_lo[t]) and roll_hi[t] > roll_lo[t]
                    and (c[t] - roll_lo[t]) / (roll_hi[t] - roll_lo[t]) <= xg.DIP_POS_MAX):
                a = float(np.clip(atr[t] / c[t], 0.008, 0.08))
                ep = {"date": dates[t],
                      "atrp": float(atr[t] / c[t]),
                      "rsi14": float(rsi14[t]),
                      "var": {}}
                if reg_dates is not None:
                    ri = int(np.searchsorted(reg_dates, dates[t], side="right")) - 1
                    ep["regime"] = ("bull" if (0 <= ri < len(reg[1]) and reg[1][ri])
                                    else "bear")
                # 三种入场共用同一事件与同一止损基准a
                fills = {}
                e_base = t + 1
                if e_base + xg.WINDOWS[0] <= n - 1 and o[e_base] > 0:
                    fills["base"] = e_base
                for mode, trig_fn in (("hi", lambda j: c[j] > h[j - 1]),
                                      ("up2", lambda j: c[j] >= c[j - 1] * 1.02)):
                    fi = None
                    for j in range(t + 1, min(t + 1 + CONFIRM_WAIT, n - 1)):
                        if trig_fn(j):
                            fi = j + 1
                            break
                    if fi is not None and fi + xg.WINDOWS[0] <= n - 1 and o[fi] > 0:
                        fills[mode] = fi
                    else:
                        fills[mode] = None          # skip (无回弹确认)
                for mode, fi in fills.items():
                    if fi is None:
                        ep["var"][mode] = None
                    else:
                        cells = xg._sim(o, h, l, c, fi, float(o[fi]), float(o[fi]),
                                        a, None, cost)
                        if PROD in cells:
                            ep["var"][mode] = {"cells": {PROD: cells[PROD],
                                                         BEST: cells.get(BEST)},
                                               "sim_date": dates[fi - 1]}
                if ep["var"].get("base"):
                    eps.append(ep)
                    next_ok = e_base + xg.HOLD + xg.COOLDOWN
            t += xg.STRIDE
        if ci % 1000 == 0:
            log.info("fastentry %d/%d (eps %d)", ci, len(data), len(eps))
    log.info("fastentry 完成: %d dip episodes", len(eps))
    return eps


def is_fast(e):
    return e["rsi14"] <= 28.0 and e["atrp"] >= 0.05


def agg_var(eps, mode, cell):
    rows = [e["var"][mode]["cells"][cell] for e in eps
            if e["var"].get(mode) and e["var"][mode]["cells"].get(cell)]
    if not rows:
        return None
    rets = [r[0] for r in rows]
    return {"n": len(rows),
            "win": round(sum(1 for r in rows if r[2] == "won") / len(rows), 3),
            "avg_ret": round(float(np.mean(rets)), 4),
            "med_ret": round(float(np.median(rets)), 4)}


def line(name, a, extra=""):
    if a is None:
        return f"  {name:<26} (无样本)"
    return (f"  {name:<26} n={a['n']:>4}  达标 {a['win']*100:5.1f}%  "
            f"均净 {a['avg_ret']*100:+6.2f}%  中位 {a['med_ret']*100:+6.2f}%{extra}")


def main():
    eps_all = scan_variants()
    fast_all = [e for e in eps_all if is_fast(e)]
    sub = lambda rows, lo, hi=None: [e for e in rows
                                     if e["date"] >= lo and (hi is None or e["date"] < hi)]
    wins = {"3y": sub(fast_all, CUT3), "val_20-23": sub(fast_all, VAL_LO, VAL_HI),
            "6y": sub(fast_all, CUT6)}
    out = {"meta": {"confirm_wait": CONFIRM_WAIT, "gate_5d": GATE_5D,
                    "note": "幸存者偏差: 绝对数偏乐观; 确认模式止损距按信号日ATR"}}

    # ---- A. 入场方式对比 ----
    for cell in (PROD, BEST):
        print(f"\n===== 入场方式对比 · {cell} =====")
        for wname, rows in wins.items():
            for mode, label in (("base", "现行(次日开盘)"), ("hi", "回弹确认(破前日高)"),
                                ("up2", "回弹确认(+2%日)")):
                a = agg_var(rows, mode, cell)
                filled = sum(1 for e in rows if e["var"].get(mode))
                skip = f"  跳过 {(1 - filled/len(rows))*100:4.1f}%" if rows and mode != "base" else ""
                print(line(f"{wname} {label}", a, skip))
                out[f"{cell}|{wname}|{mode}"] = a
            # 被确认跳过的信号按base进会怎样 (确认的价值来源)
            skipped = [e for e in rows if not e["var"].get("hi")]
            a_skip = agg_var(skipped, "base", cell)
            print(line(f"{wname} [被hi跳过者若base进]", a_skip))
            out[f"{cell}|{wname}|skipped_base"] = a_skip

    # ---- B. 连串深度诊断 (base入场) ----
    print(f"\n===== 连串深度 (信号日前5交易日全市场快弹数) · base · {BEST} =====")
    date_cnt = defaultdict(int)
    for e in fast_all:
        date_cnt[e["date"]] += 1
    all_dates = sorted(date_cnt)
    didx = {d: i for i, d in enumerate(all_dates)}
    def depth(d):
        i = didx[d]
        return sum(date_cnt[all_dates[j]] for j in range(max(0, i - 5), i))
    buckets = {"孤立(0-2)": lambda x: x <= 2, "小串(3-15)": lambda x: 3 <= x <= 15,
               "大串(16+)": lambda x: x >= 16}
    for wname, rows in (("3y", wins["3y"]), ("6y", wins["6y"])):
        for bname, cond in buckets.items():
            grp = [e for e in rows if cond(depth(e["date"]))]
            print(line(f"{wname} {bname}", agg_var(grp, "base", BEST)))
            out[f"cluster|{wname}|{bname}"] = agg_var(grp, "base", BEST)

    # ---- C. 大盘急跌门 (SPY 5日收益 >= -4% 才接新信号) ----
    idx = ps.load_index()
    ic, idates = idx["ohlcv"][:, 3], list(idx["dates"])
    r5 = np.full(len(ic), np.nan)
    r5[5:] = ic[5:] / ic[:-5] - 1.0
    imap = {d: i for i, d in enumerate(idates)}
    def gate_ok(d):
        i = imap.get(d)
        return i is not None and not np.isnan(r5[i]) and r5[i] >= GATE_5D
    print(f"\n===== 大盘急跌门 (SPY5日>= {GATE_5D*100:.0f}%) · base · {BEST} =====")
    for wname, rows in (("3y", wins["3y"]), ("6y", wins["6y"])):
        for gname, grp in (("门内(可进)", [e for e in rows if gate_ok(e["date"])]),
                           ("门外(急跌中)", [e for e in rows if not gate_ok(e["date"])])):
            print(line(f"{wname} {gname}", agg_var(grp, "base", BEST)))
            out[f"gate|{wname}|{gname}"] = agg_var(grp, "base", BEST)

    # ---- D. $10k 组合复验 (5槽, $1/边) ----
    print(f"\n===== $10k 组合模拟 · 5槽 · {BEST} =====")
    def rows_for(rows, mode, gated):
        outr = []
        for e in rows:
            va = e["var"].get(mode)
            if not va or not va["cells"].get(BEST):
                continue
            if gated and not gate_ok(e["date"]):
                continue
            outr.append({"date": va["sim_date"], "cells": {BEST: va["cells"][BEST]}})
        return outr
    axis_all = idates
    for wname, lo, hi in (("3y", CUT3, None), ("val_20-23", VAL_LO, VAL_HI), ("6y", CUT6, None)):
        axis = [d for d in axis_all if d >= lo and (hi is None or d < hi)]
        rows = sub(fast_all, lo, hi)
        for mode, gated, label in (("base", False, "现行入场"), ("hi", False, "回弹确认"),
                                   ("base", True, "现行+急跌门"), ("hi", True, "确认+急跌门")):
            r = fp.simulate(rows_for(rows, mode, gated), BEST, axis, slots=5)
            out[f"sim|{wname}|{label}"] = r
            print(f"  {wname:<10} {label:<10} 期末 ${r['end']:>9,.0f}  年化 {r['cagr']*100:+6.2f}%  "
                  f"回撤 {r['mdd']*100:6.2f}%  {r['trades_yr']:5.1f}笔/年  占用 {r['util']*100:4.0f}%")

    with open(os.path.join("data", "fastentry_research.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("\n-> data/fastentry_research.json")


if __name__ == "__main__":
    main()
