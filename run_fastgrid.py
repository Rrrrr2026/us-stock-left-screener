# -*- coding: utf-8 -*-
"""⚡快弹 RSI 参数网格 + 多窗口重放 (美股)。

问题 (用户 2026-09-01): ①快弹回测的止损到底是多少/量化期望多少 ②看5年收益
③RSI周期×阈值 3年选优, 再用不重叠前3年+全6年验证是否只是过拟合。
方法: 复用 exitgrid 的 dip 事件流与 _sim 出场内核 (生产格 s18f5|t10|w20),
每个事件额外记录 RSI(6/10/14/21) 与 ATR%、s18f5 实际止损距。
自检: (p=14, thr=28) 格在近3年必须复现 run_dip3y 的 n=1130 / win53.0% / +0.91%。
幸存者声明: 仅现存股票, 绝对数偏乐观, 结论只做格间相对比较。
"""
import io
import json
import logging
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
from screener import market as mkt                      # noqa: F401,E402
from leftside_core import exitgrid as xg                # noqa: E402
from leftside_core import pricestore as ps              # noqa: E402

import numpy as np                                      # noqa: E402
from numpy.lib.stride_tricks import sliding_window_view  # noqa: E402

PERIODS = (6, 10, 14, 21)
THRESHOLDS = (20.0, 24.0, 28.0, 32.0)
ATRP_MIN = 0.05
CELL = "s18f5|t10|w20"
CUT3, CUT5, CUT6 = "2023-09-01", "2021-09-01", "2020-09-01"
VAL_LO, VAL_HI = "2020-09-01", "2023-09-01"   # 与调参窗不重叠的前3年
MIN_N_PICK = 200


def _rsi_multi(c):
    d = np.diff(c, prepend=c[0])
    up, dn = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
    out = {}
    for p in PERIODS:
        g, l = xg._wilder(up, p), xg._wilder(dn, p)
        with np.errstate(divide="ignore", invalid="ignore"):
            r = 100.0 - 100.0 / (1.0 + g / np.where(l == 0, np.nan, l))
        out[p] = np.where((l == 0) & (g > 0), 100.0, r)
    return out


def scan():
    m = xg.current()
    min_turn = xg.MIN_TURNOVER.get(m.name, 5e6)
    cost = m.cost_rt
    data = ps.load(sorted(ps.last_dates()))
    log.info("fastgrid: %d 只票", len(data))
    eps = []
    for ci, (code, ser) in enumerate(data.items(), 1):
        dates, ohlcv = ser["dates"], ser["ohlcv"]
        n = len(dates)
        if n < 320:
            continue
        o, h, l, c, v = (ohlcv[:, 0], ohlcv[:, 1], ohlcv[:, 2], ohlcv[:, 3], ohlcv[:, 4])
        ma, rsi14, atr = xg._ind(o, h, l, c)
        rmulti = _rsi_multi(c)
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
                e = t + 1
                fill = float(o[e])
                if fill > 0 and e + xg.WINDOWS[0] <= n - 1:
                    a = float(np.clip(atr[t] / c[t], 0.008, 0.08))
                    cells = xg._sim(o, h, l, c, e, fill, fill, a, None, cost)
                    if CELL in cells:
                        eps.append({
                            "date": dates[t],
                            "atrp": float(atr[t] / c[t]),
                            "sret": float(np.clip(1.8 * a, 0.05, 0.15)),
                            "rsi": {p: (float(rmulti[p][t])
                                        if not np.isnan(rmulti[p][t]) else None)
                                    for p in PERIODS},
                            "cell": cells[CELL],
                        })
                        next_ok = e + xg.HOLD + xg.COOLDOWN
            t += xg.STRIDE
        if ci % 1000 == 0:
            log.info("fastgrid %d/%d (eps %d)", ci, len(data), len(eps))
    log.info("fastgrid 完成: %d dip episodes", len(eps))
    return eps


def agg(rows):
    if not rows:
        return None
    rets = [r["cell"][0] for r in rows]
    return {"n": len(rows),
            "win": round(sum(1 for r in rows if r["cell"][2] == "won") / len(rows), 3),
            "stop_rate": round(sum(1 for r in rows if r["cell"][2] == "stopped") / len(rows), 3),
            "avg_ret": round(float(np.mean(rets)), 4),
            "med_ret": round(float(np.median(rets)), 4),
            "avg_sret": round(float(np.mean([r["sret"] for r in rows])), 4)}


def fmt(name, a):
    if a is None:
        return f"  {name:<22} (无样本)"
    return (f"  {name:<22} n={a['n']:>5}  达标 {a['win']*100:5.1f}%  止损率 {a['stop_rate']*100:5.1f}%  "
            f"均净 {a['avg_ret']*100:+6.2f}%  中位 {a['med_ret']*100:+6.2f}%  均止损距 {a['avg_sret']*100:4.1f}%")


def main():
    eps = scan()
    sub = lambda lo, hi=None: [e for e in eps
                               if e["date"] >= lo and (hi is None or e["date"] < hi)]

    def fast(rows, p, thr):
        return [e for e in rows
                if e["rsi"].get(p) is not None and e["rsi"][p] <= thr
                and e["atrp"] >= ATRP_MIN]

    out = {"meta": {"cell": CELL, "atrp_min": ATRP_MIN, "cut": [CUT3, CUT5, CUT6],
                    "val_window": [VAL_LO, VAL_HI], "note": "幸存者偏差: 绝对数偏乐观"}}

    # -- 自检 + Q1/Q2: 生产快弹(14,28) 各窗口 --
    print("\n===== 生产口径 ⚡快弹 (RSI14<=28 & ATR%>=5) · 各时间窗 =====")
    prod = {}
    for name, rows in (("3y", sub(CUT3)), ("5y", sub(CUT5)), ("6y", sub(CUT6)),
                       ("9y", eps), ("val_20-23", sub(VAL_LO, VAL_HI))):
        a = agg(fast(rows, 14, 28.0))
        prod[name] = a
        print(fmt(name, a))
    out["prod_fast"] = prod
    a3 = prod["3y"]
    ok = a3 and a3["n"] == 1130 and abs(a3["win"] - 0.530) < 0.006
    print(f"  [自检 vs run_dip3y: n=1130/53.0% -> {'PASS' if ok else 'MISMATCH!'}]")

    # -- Q3: 网格 3年调参 --
    r3, rval, r6 = sub(CUT3), sub(VAL_LO, VAL_HI), sub(CUT6)
    print(f"\n===== RSI 周期×阈值 网格 · 近3年 (每格: n/达标/均净) =====")
    grid = {}
    for p in PERIODS:
        line_ = [f"p={p:<3}"]
        for thr in THRESHOLDS:
            a = agg(fast(r3, p, thr))
            grid[f"p{p}_t{int(thr)}"] = {"tune_3y": a}
            line_.append(f"t{int(thr)}: " + (f"{a['n']:>4}/{a['win']*100:4.1f}%/{a['avg_ret']*100:+5.2f}%"
                                             if a else "  --  "))
        print("  " + "  |  ".join(line_))

    ranked = sorted((k for k, v in grid.items()
                     if v["tune_3y"] and v["tune_3y"]["n"] >= MIN_N_PICK),
                    key=lambda k: -grid[k]["tune_3y"]["avg_ret"])
    print(f"\n===== 全网格验证 (调参3y | 验证20-23 | 全6y), 按3年均净排序, n>={MIN_N_PICK} =====")
    for k in ranked:
        p = int(k.split("_")[0][1:]); thr = float(k.split("_t")[1])
        av, a6 = agg(fast(rval, p, thr)), agg(fast(r6, p, thr))
        grid[k]["val_20_23"], grid[k]["all_6y"] = av, a6
        t = grid[k]["tune_3y"]
        mark = " <-- 生产" if k == "p14_t28" else ""
        print(f"  {k:<10} 3y: n={t['n']:>4} {t['win']*100:4.1f}% {t['avg_ret']*100:+5.2f}%"
              f"   验证20-23: " + (f"n={av['n']:>4} {av['win']*100:4.1f}% {av['avg_ret']*100:+5.2f}%" if av else "--")
              + f"   全6y: " + (f"n={a6['n']:>4} {a6['win']*100:4.1f}% {a6['avg_ret']*100:+5.2f}%" if a6 else "--")
              + mark)
    out["grid"] = grid
    out["ranked_3y"] = ranked

    with open(os.path.join("data", "fastgrid_research.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("\n-> data/fastgrid_research.json")


log = logging.getLogger("fastgrid")

if __name__ == "__main__":
    main()
