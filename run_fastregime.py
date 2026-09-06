# -*- coding: utf-8 -*-
"""⚡快弹 出场候选 s25f7|tR 的 分窗 / 逐年 / 牛熊体制 稳定性 (审计任务 2026-09-05)。

背景: 此前档案写 "s25f7|tR 三窗稳定 +2.05/+2.06/+2.06%/笔", 但 6y 窗 (2020-09..) 是
val(2020-09..2023-09) 与 3y(2023-09..) 的并集 — 只有两个独立窗口, 且候选从未在
2016-08..2020-09 (基线 s18f5|t10 均约 -5%/笔的年代) 上测过。
方法: 复用 run_fastgrid.scan() 事件流 (exitgrid._sim 内核, 不重写引擎), 取生产快弹子集
(RSI14<=28 & ATR%>=5), 对 4 格 s18f5|t10 (生产) / s25f7|t10 / s18f5|tR / s25f7|tR 在
四个互不重叠窗口 early / val / recent + 逐历年 上分列, 每窗再按 episode['regime']
(SPY 收盘>=MA50 为 bull) 拆牛熊; 同一事件四格共享成交价, 故另给 候选-生产 的配对差
(均值 / 标准误 / t 值 / 正差占比)。
自检: recent 窗 s18f5|t10 必须复现 n=1130 / 53.0% / +0.91%, s25f7|tR +2.05%; val 窗 n=865 / +2.06%。
幸存者声明: 仅现存股票, 绝对数偏乐观; 本脚本只做格间相对比较。价格库 2016-08 起, 扫描需
300 根热身 + 250 日滚动高低, 故 early 窗实际首个事件约 2017-10。
输出: data/fastregime_research.json
"""
import json
import logging
import os
import sys
from collections import Counter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import numpy as np                                      # noqa: E402

import run_fastgrid as fg                               # noqa: E402  (已包装stdout为UTF-8, 勿重复包装)

log = logging.getLogger("fastregime")

PROD = "s18f5|t10|w20"
CELLS = (PROD, "s25f7|t10|w20", "s18f5|tR|w20", "s25f7|tR|w20")
WINDOWS = (("early", "2016-08-01", "2020-09-01"),
           ("val", "2020-09-01", "2023-09-01"),
           ("recent", "2023-09-01", None))
RSI_P, RSI_MAX = 14, 28.0


def fast(rows):
    return [e for e in rows
            if e["rsi"].get(RSI_P) is not None and e["rsi"][RSI_P] <= RSI_MAX
            and e["atrp"] >= fg.ATRP_MIN]


def sub(rows, lo, hi=None):
    return [e for e in rows if e["date"] >= lo and (hi is None or e["date"] < hi)]


def paired(rows, cell, base=PROD):
    """同事件配对差 cell - base (同一成交价, 只差出场规则)。"""
    d = np.array([e["cells"][cell][0] - e["cells"][base][0] for e in rows
                  if cell in e["cells"] and base in e["cells"]], dtype=float)
    if len(d) == 0:
        return None
    se = float(d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 1 else None
    return {"n": int(len(d)), "mean_diff": round(float(d.mean()), 4),
            "se": round(se, 4) if se is not None else None,
            "t": round(float(d.mean() / se), 2) if se else None,
            "pos_share": round(float((d > 0).mean()), 3),
            "zero_share": round(float((d == 0).mean()), 3)}


def block(rows):
    """一个窗口 (或一年 / 一个体制) 的完整统计。"""
    out = {"n": len(rows)}
    if rows:
        out["first"], out["last"] = min(e["date"] for e in rows), max(e["date"] for e in rows)
        ym, k = Counter(e["date"][:7] for e in rows).most_common(1)[0]
        out["top_month"] = {"month": ym, "n": k, "share": round(k / len(rows), 3)}
    out["cells"] = {c: fg.agg(rows, c) for c in CELLS}
    out["paired_vs_prod"] = {c: paired(rows, c) for c in CELLS if c != PROD}
    reg = {}
    for r in ("bull", "bear"):
        rr = [e for e in rows if e.get("regime") == r]
        reg[r] = {"n": len(rr), "cells": {c: fg.agg(rr, c) for c in CELLS},
                  "paired_vs_prod": {c: paired(rr, c) for c in CELLS if c != PROD}}
    out["regime"] = reg
    return out


def cellfmt(a):
    if a is None:
        return f"{'--':^22}"
    return f"{a['win']*100:4.1f}%/{a['avg_ret']*100:+5.2f}/{a['med_ret']*100:+5.2f}"


def print_table(title, blocks):
    print(f"\n===== {title} · 每格: 达标%/均净%/中位% =====")
    print(f"  {'窗口':<14}{'n':>6}  " + "  ".join(f"{c:^22}" for c in CELLS))
    for name, b in blocks:
        print(f"  {name:<14}{b['n']:>6}  " + "  ".join(cellfmt(b["cells"][c]) for c in CELLS))


def main():
    eps = fg.scan()
    feps = fast(eps)
    out = {"meta": {"cells": list(CELLS), "prod": PROD,
                    "fast_rule": f"RSI{RSI_P}<={RSI_MAX:g} & ATR%>={fg.ATRP_MIN}",
                    "windows": [list(w) for w in WINDOWS],
                    "n_dip_all": len(eps), "n_fast_all": len(feps),
                    "first_episode": min(e["date"] for e in feps) if feps else None,
                    "last_episode": max(e["date"] for e in feps) if feps else None,
                    "note": "幸存者偏差: 仅现存股票, 绝对数偏乐观; 只做格间相对比较. "
                            "early 窗因 300 根热身实际自 ~2017-10 起."}}

    # ---- 三个互不重叠窗口 + 全期 ----
    win_blocks = []
    for name, lo, hi in WINDOWS:
        win_blocks.append((name, block(sub(feps, lo, hi))))
    win_blocks.append(("all", block(feps)))
    out["windows"] = {k: v for k, v in win_blocks}
    # 并集核对: 旧 "6y" = val ∪ recent
    n6 = len(sub(feps, "2020-09-01"))
    out["meta"]["union_check"] = {"n_6y": n6,
                                  "n_val_plus_recent": out["windows"]["val"]["n"] + out["windows"]["recent"]["n"],
                                  "identical": n6 == out["windows"]["val"]["n"] + out["windows"]["recent"]["n"]}
    print_table("快弹子集 · 三个互不重叠窗口 (early=2016-08..2020-09 / val=2020-09..2023-09 / recent=2023-09..)",
                win_blocks)
    for name, b in win_blocks:
        tm = b.get("top_month")
        if tm:
            print(f"    {name:<10} 事件区间 {b['first']}..{b['last']}  最密月 {tm['month']} 占 {tm['share']*100:4.1f}%")
    print(f"  [并集核对] 旧6y n={n6} == val+recent n={out['meta']['union_check']['n_val_plus_recent']}"
          f" -> {'同一批事件, 非独立窗口' if out['meta']['union_check']['identical'] else '不等?!'}")

    # ---- 自检 ----
    rc, vc = out["windows"]["recent"]["cells"], out["windows"]["val"]["cells"]
    chk = (rc[PROD] and rc[PROD]["n"] == 1130 and abs(rc[PROD]["win"] - 0.530) < 0.006
           and abs(rc[PROD]["avg_ret"] - 0.0091) < 0.0006
           and abs(rc["s25f7|tR|w20"]["avg_ret"] - 0.0205) < 0.0006
           and vc[PROD] and vc[PROD]["n"] == 865 and abs(vc["s25f7|tR|w20"]["avg_ret"] - 0.0206) < 0.0006)
    out["meta"]["selfcheck"] = bool(chk)
    print(f"  [自检 vs fastgrid/fastport 档案: recent n=1130/53.0%/+0.91%, s25f7|tR +2.05%; val n=865/+2.06%"
          f" -> {'PASS' if chk else 'MISMATCH!'}]")

    # ---- 牛熊体制 × 窗口 ----
    reg_blocks = []
    for name, b in win_blocks:
        for r in ("bull", "bear"):
            reg_blocks.append((f"{name}·{r}", b["regime"][r]))
    print_table("牛熊体制 (SPY>=MA50=bull) × 窗口", reg_blocks)

    # ---- 逐历年 ----
    years = sorted({e["date"][:4] for e in feps})
    yr_blocks = [(y, block([e for e in feps if e["date"][:4] == y])) for y in years]
    out["years"] = {k: v for k, v in yr_blocks}
    print_table("逐历年 (2026 至 8 月, 末尾 ~30 bar 事件不完整已剔)", yr_blocks)
    reg_yr = []
    for y, b in yr_blocks:
        for r in ("bull", "bear"):
            if b["regime"][r]["n"]:
                reg_yr.append((f"{y}·{r}", b["regime"][r]))
    print_table("逐历年 × 牛熊", reg_yr)

    # ---- 配对差 汇总 ----
    print(f"\n===== 配对差 (候选 - 生产 {PROD}, 同事件同成交价) · 均差% (t值) [正差占比] =====")
    print(f"  {'窗口':<14}" + "".join(f"{c:^26}" for c in CELLS if c != PROD))
    for name, b in win_blocks + yr_blocks:
        cells = []
        for c in CELLS:
            if c == PROD:
                continue
            p = b["paired_vs_prod"][c]
            cells.append(f"{p['mean_diff']*100:+5.2f} (t={p['t']:+5.1f}) [{p['pos_share']*100:3.0f}%]"
                         if p and p["t"] is not None else f"{'--':^24}")
        print(f"  {name:<14}" + "".join(f"{x:^26}" for x in cells))

    summ = {}
    for c in CELLS:
        pos_years = [y for y, b in yr_blocks if b["cells"][c] and b["cells"][c]["avg_ret"] > 0]
        beat_years = [y for y, b in yr_blocks
                      if c != PROD and b["paired_vs_prod"][c] and b["paired_vs_prod"][c]["mean_diff"] > 0]
        summ[c] = {"years_total": len(yr_blocks), "years_positive": pos_years,
                   "years_beat_prod": beat_years if c != PROD else None,
                   "windows_positive": [n for n, b in win_blocks[:3] if b["cells"][c] and b["cells"][c]["avg_ret"] > 0],
                   "windows_beat_prod": ([n for n, b in win_blocks[:3]
                                         if b["paired_vs_prod"][c] and b["paired_vs_prod"][c]["mean_diff"] > 0]
                                        if c != PROD else None)}
    out["summary"] = summ
    print("\n===== 汇总: 逐年为正 / 逐年胜生产 / 独立窗为正 / 独立窗胜生产 =====")
    for c, s in summ.items():
        print(f"  {c:<14} 正年 {len(s['years_positive'])}/{s['years_total']}  "
              f"胜生产年 {len(s['years_beat_prod']) if s['years_beat_prod'] is not None else '--'}/{s['years_total']}  "
              f"正窗 {len(s['windows_positive'])}/3  "
              f"胜生产窗 {len(s['windows_beat_prod']) if s['windows_beat_prod'] is not None else '--'}/3")

    os.makedirs("data", exist_ok=True)
    with open(os.path.join("data", "fastregime_research.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("\n-> data/fastregime_research.json")


if __name__ == "__main__":
    main()
