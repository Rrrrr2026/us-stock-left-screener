#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键运行 (美股左侧支撑位筛选)
    python run_pipeline.py
流程: 板块景气(GICS/ETF) -> 技术左侧扫描(标普500) -> 基本面 -> 交叉打分 -> 入库 -> 导出看板
"""
from __future__ import annotations
import os
import sys
import time
import argparse
import logging
import statistics
import datetime as dt
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from screener.config import CONFIG
from screener import db
from screener import datasource as ds
from screener import module1_industry as m1
from screener import module2_tech as m2
from screener import module3_fundamentals as m3
from screener import module4_crossscore as m4
from screener import export_data as ex

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("screener.run")


def _tqdm():
    try:
        from tqdm import tqdm
        return tqdm
    except Exception:
        return lambda x, **k: x


def run(use_cache=True):
    tqdm = _tqdm()
    run_date = dt.date.today().isoformat()
    started = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    CONFIG["source"]["use_cache"] = use_cache

    db.init_db()
    db.clear_run(run_date)

    log.info("拉取股票池 (标普500) ...")
    universe = ds.get_universe()
    if universe is None or universe.empty:
        log.error("股票池获取失败, 退出")
        return
    log.info("股票池: %d 只", len(universe))

    log.info("模块1: 计算板块景气度 ...")
    sec_df = m1.compute_sector_scores(
        universe, progress_cb=lambda i, n, s: log.info("  板块 %d/%d %s", i, n, s))
    if sec_df is not None and not sec_df.empty:
        db.save_industry_scores(run_date, sec_df)
    prosperity_map = dict(zip(sec_df["industry"], sec_df["prosperity_score"])) if (sec_df is not None and not sec_df.empty) else {}
    selected = list(sec_df[sec_df["selected"]]["industry"]) if (sec_df is not None and not sec_df.empty) else []
    log.info("板块景气榜前3: %s", list(sec_df["industry"][:3]) if (sec_df is not None and not sec_df.empty) else [])

    stocks = [(r["code"], r["name"], r["sector"]) for _, r in universe.iterrows()]
    workers = CONFIG["fetch"]["max_workers"] or min(12, (os.cpu_count() or 4) * 2)

    _bench = ds.fetch_benchmark()
    bench_close = _bench["close"] if (_bench is not None and not _bench.empty) else None

    # ---- 阶段A: 技术扫描 ----
    def _scan(code, name, sector):
        h = ds.fetch_hist(code)
        if h is None:
            return None
        rec, detail = m2.scan_one(code, name, h, None, bench_close=bench_close)
        if rec is None or rec["tech_score"] < CONFIG["tech"]["min_tech_score"]:
            return None
        rec["industry"] = sector
        return (rec, detail)

    log.info("阶段A 技术扫描: %d 只, 并发 %d 线程 ...", len(stocks), workers)
    hits, n_scanned = [], 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_scan, c, n, s) for (c, n, s) in stocks]
        for fut in tqdm(as_completed(futs), total=len(futs)):
            n_scanned += 1
            try:
                r = fut.result()
            except Exception as e:
                log.debug("扫描失败: %s", e)
                continue
            if r:
                hits.append(r)
    log.info("技术命中 %d 只", len(hits))

    # ---- 阶段B: 仅对技术分最高的前N只拉基本面 ----
    hits.sort(key=lambda rd: (-rd[0]["tech_score"], rd[0]["code"]))
    top_hits = hits[:CONFIG["output"]["fund_top_n"]]
    log.info("阶段B 基本面+交叉打分: 取技术分最高 %d 只 ...", len(top_hits))

    def _fund(rd):
        rec, detail = rd
        f = m3.pull_fundamentals(rec["code"], sector=rec.get("industry"))
        return (rec, detail, f)

    # 预热 Yahoo crumb/cookie(顺序拉2只), 基本面用更低并发, 显著减少 401 Invalid Crumb
    for rd in top_hits[:2]:
        try:
            ds.fetch_info(rd[0]["code"])
        except Exception:
            pass
    fund_workers = CONFIG["fetch"].get("fund_workers") or workers
    results = []
    with ThreadPoolExecutor(max_workers=fund_workers) as pool:
        futs = [pool.submit(_fund, rd) for rd in top_hits]
        for fut in tqdm(as_completed(futs), total=len(futs)):
            try:
                results.append(fut.result())
            except Exception as e:
                log.debug("基本面失败: %s", e)

    # 板块PE中位 + 全体PE横截面分位 (使 PE 便宜加分与"分位"列有意义)
    all_pe = sorted([f["pe_ttm"] for (_, _, f) in results if f.get("pe_ttm") and f["pe_ttm"] > 0])
    sec_pe = defaultdict(list)
    for (rec, _, f) in results:
        if f.get("pe_ttm") and f["pe_ttm"] > 0:
            sec_pe[rec.get("industry")].append(f["pe_ttm"])
    sec_pe_med = {s: statistics.median(v) for s, v in sec_pe.items() if v}

    def _pe_pct(pe):
        if not pe or pe <= 0 or not all_pe:
            return None
        return round(sum(1 for x in all_pe if x <= pe) / len(all_pe) * 100.0, 1)

    scored = []
    for (rec, detail, f) in results:
        sec = rec.get("industry")
        if f.get("pe_ttm") and f["pe_ttm"] > 0 and sec in sec_pe_med:
            f["pe_industry_median"] = round(sec_pe_med[sec], 2)
            f["pe_vs_industry"] = round(f["pe_ttm"] / sec_pe_med[sec], 2)
        f["pe_pct"] = _pe_pct(f.get("pe_ttm"))
        fr = m4.cross_score(rec, f, prosperity_map.get(sec))
        scored.append((rec, detail, f, fr))

    scored.sort(key=lambda x: (-(x[3].get("final_score") or -1), x[0]["code"]))
    detail_n = CONFIG["output"]["dashboard_detail_top_n"]
    final_records = []
    for idx, (rec, detail, f, fr) in enumerate(scored):
        db.save_tech(run_date, [rec])
        db.save_fundamental(run_date, rec["code"], f)
        db.save_final(run_date, [fr])
        final_records.append(fr)
        if idx < detail_n and detail:
            db.save_detail(run_date, rec["code"], detail)

    finished = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.log_run(run_date, started, finished, n_scanned, len(final_records), selected, "ok")
    log.info("扫描完成: 扫描 %d, 命中 %d", n_scanned, len(final_records))
    ex.write_dashboard_js(run_date)
    ex.write_csv(run_date)
    log.info("✅ 全部完成。请双击打开 dashboard/index.html")


def main():
    ap = argparse.ArgumentParser(description="美股左侧支撑位筛选")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()
    t0 = time.time()
    try:
        run(use_cache=not args.no_cache)
    except KeyboardInterrupt:
        log.warning("用户中断")
        sys.exit(1)
    log.info("耗时 %.1f 秒", time.time() - t0)


if __name__ == "__main__":
    main()
