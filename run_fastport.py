# -*- coding: utf-8 -*-
"""⚡快弹 深挖: ①止损×目标格子搜索 ②体制自适应RSI ③$10,000 实盘模拟($1/边手续费)。

复用 run_fastgrid.scan() 的事件流 (dip族, 已过审计的 exitgrid._sim 内核)。
组合模拟: 按真实信号时间线逐笔撮合, N个仓位槽, 每笔 = 当前权益/N, 信号多于空槽时
按当日顺序先到先得; 交易日轴取 SPY 日历; 回撤为已实现权益曲线(盘中浮亏未标记, 偏浅)。
幸存者声明照旧: 绝对数偏乐观。
"""
import io
import json
import logging
import os
import sys
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import numpy as np                                      # noqa: E402

import run_fastgrid as fg                               # noqa: E402  (它已包装 stdout 为 UTF-8, 此处不可重复包装)
from leftside_core import pricestore as ps              # noqa: E402

CUT3, CUT6 = "2023-09-01", "2020-09-01"
VAL_LO, VAL_HI = "2020-09-01", "2023-09-01"
PROD = "s18f5|t10|w20"


def fast(rows, p=14, thr=28.0):
    return [e for e in rows
            if e["rsi"].get(p) is not None and e["rsi"][p] <= thr
            and e["atrp"] >= fg.ATRP_MIN]


def fmt(name, a):
    if a is None:
        return f"  {name:<16} (无样本)"
    return (f"  {name:<16} n={a['n']:>4}  达标 {a['win']*100:5.1f}%  "
            f"均净 {a['avg_ret']*100:+6.2f}%  中位 {a['med_ret']*100:+6.2f}%")


def simulate(rows, cell, idx_dates, slots=5, fee=1.0, equity0=10000.0):
    """逐笔撮合: 信号日t -> 次日开盘占槽, days后出场; 每笔名义 = 当前总权益/slots。"""
    axis = list(idx_dates)
    pos_at = {d: i for i, d in enumerate(axis)}
    byd = defaultdict(list)
    for e in sorted(rows, key=lambda x: x["date"]):
        if cell in e["cells"]:
            byd[e["date"]].append(e["cells"][cell])
    cash = equity0
    open_pos = []                       # (exit_i, ret, dollars)
    trades = wins = 0
    fees_paid = 0.0
    peak, mdd = equity0, 0.0
    occupied_days = 0
    curve_days = 0
    for i, d in enumerate(axis):
        still = []
        for exit_i, ret, dollars in open_pos:
            if exit_i <= i:
                cash += dollars * (1.0 + ret) - fee
                fees_paid += fee
            else:
                still.append((exit_i, ret, dollars))
        open_pos = still
        equity = cash + sum(p[2] for p in open_pos)     # 持仓按成本计
        for r in byd.get(d, ()):
            if len(open_pos) >= slots:
                break
            ret, days, st = r
            dollars = equity / slots
            if dollars > cash:
                dollars = cash
            if dollars <= 50:
                continue
            cash -= dollars + fee
            fees_paid += fee
            open_pos.append((i + 1 + int(days), float(ret), dollars))
            trades += 1
            wins += (st == "won")
        equity = cash + sum(p[2] for p in open_pos)
        peak = max(peak, equity)
        mdd = min(mdd, equity / peak - 1.0)
        occupied_days += len(open_pos)
        curve_days += 1
    equity = cash + sum(p[2] * (1.0 + p[1]) for p in open_pos) - fee * len(open_pos)
    years = len(axis) / 252.0
    cagr = (equity / equity0) ** (1.0 / years) - 1.0 if years > 0 and equity > 0 else -1.0
    return {"end": round(equity, 2), "cagr": round(cagr, 4), "mdd": round(mdd, 4),
            "trades": trades, "trades_yr": round(trades / years, 1),
            "win": round(wins / trades, 3) if trades else None,
            "fees": round(fees_paid, 2),
            "util": round(occupied_days / curve_days / slots, 3)}


def main():
    eps = fg.scan()
    sub = lambda lo, hi=None: [e for e in eps
                               if e["date"] >= lo and (hi is None or e["date"] < hi)]
    r3, rval, r6 = sub(CUT3), sub(VAL_LO, VAL_HI), sub(CUT6)
    f3, fval, f6 = fast(r3), fast(rval), fast(r6)
    out = {}

    # ---- ① 止损×目标 格子 (生产快弹子集) ----
    print("\n===== 快弹(RSI14<=28 & ATR>=5%) · 止损×目标 (w20) · 3y | 20-23验证 | 6y =====")
    grid = {}
    for s in ("s18f5", "s25f7", "s30f8"):
        for tgt in ("t7", "t10", "tR"):
            cell = f"{s}|{tgt}|w20"
            a3, av, a6 = fg.agg(f3, cell), fg.agg(fval, cell), fg.agg(f6, cell)
            grid[cell] = {"3y": a3, "val": av, "6y": a6}
            mark = " <-- 生产" if cell == PROD else ""
            print(f"  {cell:<14} 3y {a3['avg_ret']*100:+6.2f}% ({a3['win']*100:4.1f}%)"
                  f"  | 验证 {av['avg_ret']*100:+6.2f}% ({av['win']*100:4.1f}%)"
                  f"  | 6y {a6['avg_ret']*100:+6.2f}% ({a6['win']*100:4.1f}%)" + mark)
    out["stop_tgt_grid"] = grid

    # ---- ② 体制自适应 RSI: bear->24紧 / bull->32松, vs 固定28 ----
    print("\n===== 体制自适应RSI (bear<=24 / bull<=32) vs 固定28 · 生产出场 =====")
    def adaptive(rows):
        return [e for e in rows
                if e["rsi"].get(14) is not None and e["atrp"] >= fg.ATRP_MIN
                and e["rsi"][14] <= (24.0 if e.get("regime") == "bear" else 32.0)]
    ad = {}
    for name, rows in (("3y", r3), ("val_20-23", rval), ("6y", r6)):
        a_fix, a_ad = fg.agg(fast(rows)), fg.agg(adaptive(rows))
        ad[name] = {"fixed28": a_fix, "adaptive": a_ad}
        print(fmt(f"{name} 固定28", a_fix))
        print(fmt(f"{name} 自适应", a_ad))
    out["adaptive_rsi"] = ad

    # ---- ③ $10,000 组合模拟 ----
    idx = ps.load_index()
    axis_all = list(idx["dates"])
    print("\n===== $10,000 实盘模拟 · $1/边手续费 · 每笔=权益/槽数 =====")
    sims = {}
    for wname, lo, hi in (("3y", CUT3, None), ("val_20-23", VAL_LO, VAL_HI), ("6y", CUT6, None)):
        axis = [d for d in axis_all if d >= lo and (hi is None or d < hi)]
        rows = fast(sub(lo, hi))
        for slots in (3, 5, 10):
            for cell in (PROD, "s25f7|tR|w20"):
                r = simulate(rows, cell, axis, slots=slots)
                sims[f"{wname}|{slots}|{cell}"] = r
                if slots == 5 or (slots in (3, 10) and cell == PROD):
                    print(f"  {wname:<10} {slots:>2}槽 {cell:<14} 期末 ${r['end']:>9,.0f}  "
                          f"年化 {r['cagr']*100:+6.2f}%  最大回撤 {r['mdd']*100:6.2f}%  "
                          f"{r['trades_yr']:5.1f}笔/年  占用率 {r['util']*100:4.1f}%  "
                          f"手续费 ${r['fees']:,.0f}")
    out["sims"] = sims
    out["pool_freq"] = {"3y_signals_per_year": round(len(f3) / 3.0, 1),
                        "6y_signals_per_year": round(len(f6) / 6.0, 1)}
    print(f"\n信号池频率: 全市场快弹 {out['pool_freq']['3y_signals_per_year']}/年(近3年), "
          f"{out['pool_freq']['6y_signals_per_year']}/年(近6年)")

    with open(os.path.join("data", "fastport_research.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("-> data/fastport_research.json")


if __name__ == "__main__":
    main()
