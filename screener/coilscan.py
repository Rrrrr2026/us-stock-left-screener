#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""coilscan — 共用核心 leftside_core.coilscan 的本仓库入口。
美股 v1 只做 技术形态 × 市场温度 (点时基本面缺稳定历史源, 结果里 quality=na)。"""
from . import market as _market          # noqa: F401
import leftside_core.coilscan as _core

globals().update({k: v for k, v in vars(_core).items() if not k.startswith("__")})


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
