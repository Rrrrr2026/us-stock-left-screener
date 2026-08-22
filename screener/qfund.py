#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
优质筛选的基本面广度 (Rotating full-universe fundamentals)
==========================================================
优质榜原本只能筛"数据库碰过的 ~1300 只" (左侧候选的基本面)。这里每天只抓
股票池的 1/7 (按代码哈希分片, 稳定), 一周轮完全部 ~4000 只, 存 qfund 表:
  EPS单季同比×4 (财报日历实绩, 与候选同口径) / 最新季营收同比 (info.revenueGrowth)
  / ROE (info.returnOnEquity) / PE-TTM (info.trailingPE) / 市值 / 行业 / 下一财报日。
优质榜把 qfund 与当日候选的完整基本面合并 -> 真正的全市场筛选, 每日增量成本
约 600 只 × 2 次请求, 低并发 5-8 分钟。
"""
from __future__ import annotations
import datetime as dt
import json
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import DB_PATH

log = logging.getLogger("screener.qfund")

SHARDS = 7
WORKERS = 4
MAX_AGE_DAYS = 10

_DDL = ("CREATE TABLE IF NOT EXISTS qfund("
        "code TEXT PRIMARY KEY, asof TEXT, name TEXT, sector TEXT, industry TEXT, "
        "eps_q4_json TEXT, rev_yoy REAL, roe REAL, pe REAL, mcap REAL, next_earn TEXT)")
_COLS = ("code", "asof", "name", "sector", "industry", "eps_q4_json", "rev_yoy", "roe",
         "pe", "mcap", "next_earn")


def _shard_of(code: str) -> int:
    return sum(ord(ch) for ch in str(code)) % SHARDS


def _num(v):
    return float(v) if isinstance(v, (int, float)) and v == v else None


def _one(code: str, uname: str, usector: str) -> dict | None:
    from . import datasource as ds
    from . import module3_fundamentals as m3
    info = ds.fetch_info(code) or {}
    epsh = ds.fetch_eps_history(code) or {}
    try:
        yoy, _labels = m3._eps_yoy4(epsh.get("dates"), epsh.get("eps"))
    except Exception:
        yoy = []
    roe = _num(info.get("returnOnEquity"))
    rg = _num(info.get("revenueGrowth"))
    if not info and not epsh:
        return None
    return {
        "code": code, "asof": dt.date.today().isoformat(),
        "name": info.get("shortName") or info.get("longName") or uname,
        "sector": info.get("sector") or usector, "industry": info.get("industry"),
        "eps_q4_json": json.dumps([v for v in (yoy or []) if isinstance(v, (int, float))]),
        "rev_yoy": (rg * 100.0) if rg is not None else None,
        "roe": (roe * 100.0) if roe is not None else None,
        "pe": _num(info.get("trailingPE")), "mcap": _num(info.get("marketCap")),
        "next_earn": epsh.get("next"),
    }


def update_shard(shard: int | None = None, limit: int | None = None) -> int:
    """抓今天这一片 (默认: 日期序数 % 7), 写入 qfund; 返回写入条数。"""
    from . import datasource as ds
    uni = ds.get_universe()
    if uni is None or uni.empty:
        log.warning("股票池为空, qfund 跳过")
        return 0
    shard = (dt.date.today().toordinal() % SHARDS) if shard is None else shard
    todo = [(str(r["code"]), str(r.get("name") or ""), str(r.get("sector") or ""))
            for _, r in uni.iterrows() if _shard_of(str(r["code"])) == shard]
    if limit:
        todo = todo[:limit]
    log.info("qfund 分片 %d/%d: %d 只", shard, SHARDS, len(todo))
    rows = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(_one, c, n, s): c for (c, n, s) in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                r = fut.result()
            except Exception as e:
                log.debug("qfund %s 失败: %s", futs[fut], e)
                r = None
            if r:
                rows.append(r)
            if i % 100 == 0:
                log.info("qfund 进度 %d/%d (有效 %d)", i, len(todo), len(rows))
    if not rows:
        return 0
    conn = sqlite3.connect(DB_PATH)
    conn.execute(_DDL)
    placeholders = ",".join(["?"] * len(_COLS))
    conn.executemany(
        "INSERT OR REPLACE INTO qfund(" + ",".join(_COLS) + ") VALUES(" + placeholders + ")",
        [tuple(r.get(c) for c in _COLS) for r in rows])
    conn.commit()
    conn.close()
    log.info("qfund 写入 %d 条", len(rows))
    return len(rows)


def load_all(max_age_days: int = MAX_AGE_DAYS) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(_DDL)
    conn.row_factory = sqlite3.Row
    cutoff = (dt.date.today() - dt.timedelta(days=max_age_days)).isoformat()
    rows = [dict(r) for r in conn.execute("SELECT * FROM qfund WHERE asof >= ?", (cutoff,))]
    conn.close()
    return rows


def dom_rank_map() -> dict:
    """全股票池按 (细分行业 or 板块) 市值排名 -> {code: (rank, share%)}; 与流水线口径一致。"""
    from . import datasource as ds
    uni = ds.get_universe()
    out = {}
    if uni is None or uni.empty or "mcap" not in uni.columns:
        return out
    u = uni[uni["mcap"] > 0].copy()
    if "nasdaq_industry" in u.columns:
        u["_grp"] = u["nasdaq_industry"].where(u["nasdaq_industry"].astype(bool), u["sector"])
    else:
        u["_grp"] = u["sector"]
    for g, gg in u.groupby("_grp"):
        if not g:
            continue
        gg = gg.sort_values("mcap", ascending=False).reset_index(drop=True)
        tot = float(gg["mcap"].sum()) or 1.0
        for i, r in gg.iterrows():
            out[str(r["code"])] = (int(i) + 1, round(float(r["mcap"]) / tot * 100.0, 1))
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    update_shard()
