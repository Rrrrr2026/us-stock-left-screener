#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中央配置 (Central CONFIG) —— 美股版
====================================
所有阈值/权重/股票池/开关集中在这里。数据源: yfinance (Yahoo Finance)。
用户可见文字用简体中文;公司名/代码保持英文。
"""
from __future__ import annotations
import os

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(PKG_DIR)
DATA_DIR = os.path.join(ROOT_DIR, "data")
DASHBOARD_DIR = os.path.join(ROOT_DIR, "dashboard")
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "us_stock.db")
DASHBOARD_DATA_JS = os.path.join(DASHBOARD_DIR, "dashboard_data.js")

# 11 个 GICS 板块 -> SPDR 行业 ETF (作为板块指数代理算趋势/动量)
SECTOR_ETF = {
    "Information Technology": "XLK",
    "Health Care": "XLV",
    "Financials": "XLF",
    "Consumer Discretionary": "XLY",
    "Communication Services": "XLC",
    "Industrials": "XLI",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Materials": "XLB",
}

CONFIG = {
    "source": {
        "sp500_csv": [
            "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv",
            "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv",
        ],
        "sp500_wiki": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "benchmark": "SPY",             # 基准: 标普500 ETF
        "sector_etf": SECTOR_ETF,
        "cache_dir": os.path.join(DATA_DIR, "cache"),
        "cache_ttl_hours": 12,
        "use_cache": True,
    },
    "fetch": {
        "period": "2y",                 # yfinance 拉取历史区间 (≈500 交易日, 够 MA250)
        "auto_adjust": True,            # 复权
        "sleep_sec": 0.05,
        "max_retries": 2,
        "retry_backoff_sec": 1.0,
        "timeout_sec": 20,
        "max_workers": 0,               # 0 = min(12, CPU*2); yfinance 并发别太高防限频
    },
    "sector": {                          # 板块景气 (对应 A股的"行业景气")
        "top_n": 11,                    # 展示全部板块
        "use_full_universe": True,      # True = 扫全部标普500 (仅几百只, 很快)
        "trend_gate_enabled": False,    # 板块趋势硬门槛 (美股默认关, 全扫)
        "trend_gate_tolerance_pct": 2.0,
        "ma_short": 60,
        "ma_long": 120,
        "weights": {"trend": 0.30, "momentum": 0.30, "breadth": 0.25,
                    "capital": 0.0, "fundamental": 0.15},
        "breadth_sample": 80,
    },
    "tech": {
        "min_amount_usd": 5e6,          # 近20日日均成交额下限 (美元), 过滤流动性差
        "min_price": 3.0,               # 股价下限 (美元)
        "channel_window": 120,
        "channel_band_k": 2.0,
        "near_lower_pct": 4.0,
        "pivot_window": 10,
        "near_pivot_pct": 4.0,
        "ma_list": [60, 120, 250],
        "near_ma_pct": 3.0,
        "rsi_oversold": 38.0,
        "drawdown_min": 0.18,
        "weights": {"channel": 1.0, "pivot": 1.0, "ma": 0.8,
                    "oversold_div": 1.2, "drawdown": 0.6},
        "min_tech_score": 1.0,
        "detail_bars": 250,
    },
    "cross": {
        "w_tech": 0.50, "w_fund": 0.30, "w_prosperity": 0.20,
        "roe_good": 12.0, "roe_excellent": 18.0,
        "pe_low_percentile": 30.0, "pe_high_percentile": 80.0,
        "debt_ratio_warn": 70.0, "netprofit_yoy_good": 0.0,
        "strong_left_tech": 2.0, "strong_left_fund": 60.0,
        "strong_left_prosperity": 60.0, "fund_weak_threshold": 40.0,
    },
    "output": {
        "final_top_n": 200,
        "fund_top_n": 300,
        "dashboard_detail_top_n": 150,
    },
}


def deep_get(d: dict, path: str, default=None):
    cur = d
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur
