# -*- coding: utf-8 -*-
"""深度阈值敏感性: 复用 data/fastlive_episodes.json (无需重扫), 阈值 0/8/16/24/32。"""
import json
import logging
import os
import sys

logging.basicConfig(level=logging.INFO)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import run_fastlive as fl                               # noqa: E402  (导入链已包装stdout)

j = json.load(open("data/fastlive_episodes.json", encoding="utf-8"))
axis_all, rows_all = j["axis"], j["rows"]
WINS = (("3y", fl.CUT3, None), ("val", fl.VAL_LO, fl.VAL_HI), ("6y", fl.CUT6, None))
print("===== 深度阈值敏感性 · 等权5槽 · s25f7|tR (年化% / 回撤% / 笔每年 / 成交笔均%) =====")
for wname, lo, hi in WINS:
    axis = [d for d in axis_all if d >= lo and (hi is None or d < hi)]
    rows = [r for r in rows_all if r["date"] >= lo and (hi is None or r["date"] < hi)]
    line = [f"{wname:<4}"]
    for thr in (0, 8, 16, 24, 32):
        sub = [r for r in rows if r["depth"] >= thr]
        r = fl.simulate_risk(sub, axis, use_breaker=False, sizing="equal")
        ta = r["taken_avg"] * 100 if r.get("taken_avg") is not None else float("nan")
        line.append(f">={thr}: {r['cagr']*100:+5.1f}/{r['mdd']*100:5.1f}/{r['trades_yr']:4.1f}/{ta:+4.2f}")
    print("  " + "  |  ".join(line))
