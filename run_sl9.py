# -*- coding: utf-8 -*-
"""M3: 强左侧策略类九年重放 (美股 · 回踩MA60支撑 · 出场网格 4/5/10% x 15/20bar)。
美股无稳定点时基本面历史源 (与 M1 相同处理), quality 维度为 na。"""
import io
import logging
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

from screener import market as mkt            # noqa: F401,E402  set_market
from leftside_core import slscan              # noqa: E402

res = slscan.run(quality_at=None)
print("SL9 DONE episodes=", res["n_episodes"], flush=True)
print("GRID", res["grid"], flush=True)
