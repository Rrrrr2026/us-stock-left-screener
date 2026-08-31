# -*- coding: utf-8 -*-
"""⚡快弹 改进实盘规格 全真模拟 (用户 2026-09-01: "测试一下你的实盘看看效果如何")。

规格 (本会话敲定的最终版):
  信号: 生产快弹 (dip门槛 + RSI14<=28 + ATR%>=5), 不加确认/不加大盘门 (均被证伪)
  出场: s25f7|tR|w20 = 2.5×ATR止损(下限7%上限15%) + 目标1.4×实际止损距 + 20bar窗
  仓位: 单笔风险 = 权益×0.5% → 仓位 = 权益×0.005/止损距 (≈4%权益); 最多5仓
  熔断: 已实现权益自峰值回撤 <= -10% → 暂停新开仓 21 交易日, 重臂后峰值重置
  费用: 每笔收益已含0.2%往返成本, 另加 $1/边 手续费
输出: 3y / 20-23验证窗 / 6y 三窗模拟 + 等权对照; 事件明细落盘供独立核算。
幸存者声明照旧: 仅现存股票, 绝对数偏乐观。
"""
import json
import logging
import os
import sys
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import numpy as np                                      # noqa: E402

import run_fastgrid as fg                               # noqa: E402  (已包装stdout)
from leftside_core import pricestore as ps              # noqa: E402

log = logging.getLogger("fastlive")

CUT3, CUT6 = "2023-09-01", "2020-09-01"
VAL_LO, VAL_HI = "2020-09-01", "2023-09-01"
BEST = "s25f7|tR|w20"
RISK = 0.005
SLOTS = 5
BREAK_DD = -0.10
PAUSE = 21
FEE = 1.0
EQ0 = 10000.0


def build_rows(eps):
    """快弹事件 -> 模拟行 {date, ret, days, st, sret25}。sret25 与 exitgrid._sim 同式。"""
    rows = []
    for e in eps:
        if e["rsi14" if "rsi14" in e else "rsi"] is None:
            continue
        rsi14 = e["rsi"][14] if "rsi" in e else e.get("rsi14")
        if rsi14 is None or rsi14 > 28.0 or e["atrp"] < 0.05:
            continue
        cell = e["cells"].get(BEST)
        if cell is None:
            continue
        a = float(np.clip(e["atrp"], 0.008, 0.08))
        sret25 = float(np.clip(2.5 * a, 0.07, 0.15))
        rows.append({"date": e["date"], "ret": float(cell[0]), "days": int(cell[1]),
                     "st": cell[2], "sret": sret25})
    return rows


def simulate_risk(rows, axis, use_breaker=True, sizing="risk", eq0=EQ0):
    byd = defaultdict(list)
    for r in sorted(rows, key=lambda x: x["date"]):
        byd[r["date"]].append(r)
    cash, open_pos = eq0, []
    peak, blocked_until, trips = eq0, -1, 0
    trades = wins = 0
    taken = []
    fees = 0.0
    mdd = 0.0
    expo_sum = 0.0
    for i, d in enumerate(axis):
        still = []
        for exit_i, ret, dollars in open_pos:
            if exit_i <= i:
                cash += dollars * (1.0 + ret) - FEE
                fees += FEE
            else:
                still.append((exit_i, ret, dollars))
        open_pos = still
        equity = cash + sum(p[2] for p in open_pos)
        if use_breaker and i > blocked_until and equity / peak - 1.0 <= BREAK_DD:
            blocked_until = i + PAUSE
            trips += 1
            peak = equity                    # 重臂: 暂停期后以此为新峰值基准
        peak = max(peak, equity)
        if not use_breaker or i > blocked_until:
            for r in byd.get(d, ()):
                if len(open_pos) >= SLOTS:
                    break
                dollars = (equity * RISK / r["sret"]) if sizing == "risk" else equity / SLOTS
                dollars = min(dollars, cash)
                if dollars <= 50:
                    continue
                cash -= dollars + FEE
                fees += FEE
                open_pos.append((i + 1 + r["days"], r["ret"], dollars))
                trades += 1
                wins += (r["st"] == "won")
                taken.append(r["ret"])
        equity = cash + sum(p[2] for p in open_pos)
        mdd = min(mdd, equity / peak - 1.0)
        expo_sum += sum(p[2] for p in open_pos) / equity if equity > 0 else 0.0
    equity = cash + sum(p[2] * (1.0 + p[1]) for p in open_pos) - FEE * len(open_pos)
    years = len(axis) / 252.0
    cagr = (equity / eq0) ** (1.0 / years) - 1.0 if equity > 0 else -1.0
    return {"end": round(equity, 2), "cagr": round(cagr, 4), "mdd": round(mdd, 4),
            "trades": trades, "trades_yr": round(trades / years, 1),
            "win": round(wins / trades, 3) if trades else None,
            "fees": round(fees, 2), "trips": trips,
            "taken_avg": round(float(np.mean(taken)), 4) if taken else None,
            "avg_exposure": round(expo_sum / len(axis), 3)}


def main():
    eps = fg.scan()
    rows_all = build_rows(eps)
    idx = ps.load_index()
    axis_all = list(idx["dates"])
    aidx = {d: i for i, d in enumerate(axis_all)}
    cnt = np.zeros(len(axis_all))
    for r in rows_all:
        i = aidx.get(r["date"])
        if i is not None:
            cnt[i] += 1
    for r in rows_all:
        i = aidx.get(r["date"], 0)
        r["depth"] = int(cnt[max(0, i - 5):i].sum())
    out = {"meta": {"spec": f"{BEST}, risk {RISK}, slots {SLOTS}, breaker {BREAK_DD}/{PAUSE}d, fee ${FEE}/side",
                    "note": "幸存者偏差: 绝对数偏乐观; 回撤为成本标记的已实现曲线, 偏浅"}}
    json.dump({"axis": axis_all, "rows": rows_all},
              open(os.path.join("data", "fastlive_episodes.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    print(f"\n事件明细 -> data/fastlive_episodes.json ({len(rows_all)} 行, 供独立核算)")
    print(f"\n===== 改进实盘规格 全真模拟 · $10,000 起 =====")
    for wname, lo, hi in (("3y", CUT3, None), ("val_20-23", VAL_LO, VAL_HI), ("6y", CUT6, None)):
        axis = [d for d in axis_all if d >= lo and (hi is None or d < hi)]
        rows = [r for r in rows_all if r["date"] >= lo and (hi is None or r["date"] < hi)]
        deep = [r for r in rows if r["depth"] >= 16]
        for label, rws, kw in (
                ("规格版", rows, {}),
                ("规格版@$100k", rows, {"eq0": 100000.0}),
                ("规格版·深度>=16", deep, {}),
                ("等权·深度>=16", deep, {"use_breaker": False, "sizing": "equal"}),
                ("等权对照(昨日版)", rows, {"use_breaker": False, "sizing": "equal"})):
            r = simulate_risk(rws, axis, **kw)
            out[f"{wname}|{label}"] = r
            ta = f"{r['taken_avg']*100:+5.2f}%" if r.get("taken_avg") is not None else "  --"
            print(f"  {wname:<10} {label:<12} 期末 ${r['end']:>10,.0f}  年化 {r['cagr']*100:+6.2f}%  "
                  f"回撤 {r['mdd']*100:6.2f}%  {r['trades_yr']:5.1f}笔/年  成交笔均 {ta}  "
                  f"敞口 {r['avg_exposure']*100:4.1f}%  熔断 {r['trips']}  费 ${r['fees']:,.0f}")
    with open(os.path.join("data", "fastlive_research.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("\n-> data/fastlive_research.json")


if __name__ == "__main__":
    main()
