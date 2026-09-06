# -*- coding: utf-8 -*-
"""深度阈值敏感性: 复用 data/fastlive_episodes.json (无需重扫), 阈值 0/8/16/24/32。

2026-09-05 增 (审计任务): ① early 窗 (2016-08..2020-09, 此前从未测) ② 逐历年稳定性
③ 落盘 data/depthsweep_research.json。模拟口径不变: 等权 5 槽 / 无熔断 / $1 边 / $10k 起,
cell s25f7|tR|w20, 复用 run_fastlive.simulate_risk。
注意: depth = 信号日前 5 个交易日全市场快弹信号数 (纯因果), 高阈值只在崩盘簇内触发,
逐年样本极不均匀 (见 n_signals / trades); 逐年模拟每年从 $10k 重起, 跨年持仓按最终收益
结算在开仓年, 2026 只到 8 月。幸存者声明照旧: 绝对数偏乐观。
"""
import json
import logging
import os
import sys

logging.basicConfig(level=logging.INFO)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import run_fastlive as fl                               # noqa: E402  (导入链已包装stdout)

THRS = (0, 8, 16, 24, 32)
EARLY_LO, EARLY_HI = "2016-08-01", "2020-09-01"
WINS = (("early", EARLY_LO, EARLY_HI), ("val", fl.VAL_LO, fl.VAL_HI), ("3y", fl.CUT3, None),
        ("6y", fl.CUT6, None), ("all", EARLY_LO, None))


def run_window(axis, rows):
    res = {"n_signals": len(rows), "thr": {}}
    for thr in THRS:
        sub = [r for r in rows if r["depth"] >= thr]
        r = fl.simulate_risk(sub, axis, use_breaker=False, sizing="equal")
        r["n_eligible"] = len(sub)
        res["thr"][str(thr)] = r
    return res


def line(name, res):
    parts = [f"{name:<6}"]
    for thr in THRS:
        r = res["thr"][str(thr)]
        ta = f"{r['taken_avg']*100:+5.2f}" if r.get("taken_avg") is not None else "   --"
        parts.append(f">={thr}: {r['cagr']*100:+5.1f}/{r['mdd']*100:5.1f}/{r['trades_yr']:4.1f}/{ta}")
    return "  " + "  |  ".join(parts)


def main():
    j = json.load(open(os.path.join("data", "fastlive_episodes.json"), encoding="utf-8"))
    axis_all, rows_all = j["axis"], j["rows"]
    out = {"meta": {"cell": fl.BEST, "sim": "等权5槽/无熔断/$1边/$10k起 (fl.simulate_risk)",
                    "thresholds": list(THRS), "n_rows": len(rows_all),
                    "axis": [axis_all[0], axis_all[-1]],
                    "note": "幸存者偏差: 绝对数偏乐观; 逐年每年从$10k重起, 2026仅到8月; "
                            "early 窗事件实际自 ~2017-10 起 (300根热身)"},
           "windows": {}, "years": {}}

    print("===== 深度阈值敏感性 · 等权5槽 · s25f7|tR (年化% / 回撤% / 笔每年 / 成交笔均%) =====")
    for wname, lo, hi in WINS:
        axis = [d for d in axis_all if d >= lo and (hi is None or d < hi)]
        rows = [r for r in rows_all if r["date"] >= lo and (hi is None or r["date"] < hi)]
        res = run_window(axis, rows)
        res.update({"lo": lo, "hi": hi})
        out["windows"][wname] = res
        print(line(wname, res))

    print("\n===== 逐历年 (每年 $10k 重起; 括号内: 该年快弹信号数) =====")
    years = sorted({d[:4] for d in axis_all})
    for y in years:
        axis = [d for d in axis_all if d[:4] == y]
        rows = [r for r in rows_all if r["date"][:4] == y]
        if not rows:
            continue
        res = run_window(axis, rows)
        out["years"][y] = res
        print(line(f"{y}({len(rows)})", res))

    # 汇总: 每阈值 逐年为正的年数 / 有成交年数 / 相对阈值0的年化差
    summ = {}
    for thr in THRS:
        k = str(thr)
        ys = out["years"]
        traded = [y for y in ys if ys[y]["thr"][k]["trades"] > 0]
        pos = [y for y in traded if ys[y]["thr"][k]["cagr"] > 0]
        beat0 = [y for y in traded if ys[y]["thr"][k]["cagr"] > ys[y]["thr"]["0"]["cagr"]]
        summ[k] = {"years_total": len(ys), "years_traded": traded, "years_positive": pos,
                   "years_beat_thr0": beat0 if thr else None,
                   "windows_positive": [w for w in ("early", "val", "3y")
                                        if out["windows"][w]["thr"][k]["cagr"] > 0],
                   "cagr_by_year": {y: ys[y]["thr"][k]["cagr"] for y in ys},
                   "trades_by_year": {y: ys[y]["thr"][k]["trades"] for y in ys}}
    out["summary"] = summ
    print("\n===== 汇总: 阈值 -> 有成交年 / 为正年 / 胜阈值0年 / 独立窗(early,val,3y)为正 =====")
    for thr in THRS:
        s = summ[str(thr)]
        print(f"  >={thr:<3} 有成交 {len(s['years_traded']):>2}/{s['years_total']}  为正 {len(s['years_positive']):>2}  "
              f"胜阈值0 {len(s['years_beat_thr0']) if s['years_beat_thr0'] is not None else '--':>2}  "
              f"正窗 {len(s['windows_positive'])}/3 {s['windows_positive']}")

    with open(os.path.join("data", "depthsweep_research.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("\n-> data/depthsweep_research.json")


if __name__ == "__main__":
    main()
