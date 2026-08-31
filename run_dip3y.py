# -*- coding: utf-8 -*-
"""深跌抄底 · 过去三年重放 (美股) — 检验看板 71%/+6.1% (n_resolved=24) 是否站得住。

引擎: leftside_core.exitgrid (已过对抗审计) 的 dip 族, 生产口径格 s18f5|t10|w20
(1.8×ATR止损floor5%、+10%目标、20bar窗口、次日开盘成交、同bar先止损、扣往返成本)。
分段: 近三年(2023-09起) vs 九年全期; 平淡dip vs ⚡快弹子标签; 逐年; 牛熊。
幸存者声明: 价格库仅现存股票, 深跌族绝对期望系统性偏乐观 — 绝对数只当上限看。
"""
import io
import json
import logging
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
from screener import market as mkt              # noqa: F401,E402
from leftside_core import exitgrid              # noqa: E402

import numpy as np                              # noqa: E402

CUT3Y = "2023-09-01"
CELLS = ("s18f5|t10|w20", "s18f5|t7|w20", "s18f5|tR|w20")


def agg(eps, cell):
    rows = [e["cells"][cell] for e in eps if cell in e["cells"]]
    if not rows:
        return None
    rets = [r[0] for r in rows]
    return {"n": len(rows),
            "win": round(sum(1 for r in rows if r[2] == "won") / len(rows), 3),
            "stop_rate": round(sum(1 for r in rows if r[2] == "stopped") / len(rows), 3),
            "avg_ret": round(float(np.mean(rets)), 4),
            "med_ret": round(float(np.median(rets)), 4),
            "med_days": int(np.median([r[1] for r in rows]))}


def line(name, a):
    if a is None:
        return f"  {name:<26} (无样本)"
    return (f"  {name:<26} n={a['n']:>5}  达标率 {a['win']*100:5.1f}%  "
            f"止损率 {a['stop_rate']*100:5.1f}%  平均净 {a['avg_ret']*100:+6.2f}%  "
            f"中位净 {a['med_ret']*100:+6.2f}%  中位天数 {a['med_days']}")


def main():
    eps = [e for e in exitgrid.scan() if e["fam"] == "dip"]
    print(f"\ndip episodes 全期: {len(eps)}")
    out = {"meta": {"cut3y": CUT3Y, "cell_prod": CELLS[0],
                    "note": "幸存者偏差: 仅现存股票, 绝对数偏乐观; 入场=次日开盘(生产为限价区, 略有差异)"}}
    r3 = [e for e in eps if e["date"] >= CUT3Y]
    segs = {
        "9y_all": eps,
        "3y_all": r3,
        "3y_plain": [e for e in r3 if not e.get("fast")],
        "3y_fast": [e for e in r3 if e.get("fast")],
        "3y_bull": [e for e in r3 if e.get("regime") == "bull"],
        "3y_bear": [e for e in r3 if e.get("regime") == "bear"],
    }
    for y in ("2023", "2024", "2025", "2026"):
        segs[f"y{y}"] = [e for e in r3 if e["date"][:4] == y]
    for cell in CELLS:
        print(f"\n===== {cell} =====")
        out[cell] = {}
        for name, sub in segs.items():
            a = agg(sub, cell)
            out[cell][name] = a
            print(line(name, a))
    path = os.path.join("data", "dip3y_research.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"\n-> {path}")


if __name__ == "__main__":
    main()
