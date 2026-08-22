#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
"Why did it fall" evidence: recent headline keyword flags for mispriced candidates
=================================================================================
The mispricing card says "no visible deterioration - check the reason yourself".
This puts the evidence next to the score: pull each candidate's recent headlines
(Yahoo Finance news), flag risk keywords (downgrade / guidance cut / lawsuit /
investigation / offering / CEO departure / recall / short report ...) as 🚩.
No judgement - just the clues, so the manual check takes seconds.
"""
from __future__ import annotations
import datetime as dt
import logging

log = logging.getLogger("screener.newsflag")

KEYWORDS = [
    ("downgrade", "downgrade"), ("cuts guidance", "guidance cut"), ("lowers guidance", "guidance cut"),
    ("guidance", "guidance"), ("misses", "miss"), ("miss ", "miss"), ("lawsuit", "lawsuit"),
    ("class action", "lawsuit"), ("investigation", "investigation"), ("probe", "investigation"),
    ("sec ", "SEC"), ("offering", "offering"), ("dilut", "dilution"), ("resign", "executive change"),
    ("steps down", "executive change"), ("ceo", "executive change"), ("layoff", "layoffs"),
    ("recall", "recall"), ("fda", "FDA"), ("delist", "delisting"), ("bankrupt", "bankruptcy"),
    ("fraud", "fraud"), ("short seller", "short report"), ("short report", "short report"),
    ("activist", "activist"), ("tariff", "tariffs"), ("warning", "warning"), ("plunge", "selloff"),
    ("tumble", "selloff"), ("sinks", "selloff"),
]
MAX_ITEMS = 8
WINDOW_DAYS = 30


def _titles(code: str) -> list:
    try:
        import yfinance as yf
        items = yf.Ticker(code).news or []
        out = []
        for it in items:
            c = it.get("content") or it
            t = str(c.get("title") or "").strip()
            d = str(c.get("pubDate") or c.get("displayTime") or "")[:10]
            if not d and c.get("providerPublishTime"):
                d = dt.datetime.utcfromtimestamp(int(c["providerPublishTime"])).date().isoformat()
            u = ((c.get("canonicalUrl") or {}).get("url") if isinstance(c.get("canonicalUrl"), dict)
                 else c.get("link") or "")
            if t and d:
                out.append((d, t, u or ""))
        out.sort(key=lambda x: x[0], reverse=True)
        return out
    except Exception as e:
        log.debug("news %s failed: %s", code, e)
        return []


def flags_in(title: str) -> list:
    low = title.lower()
    f = []
    for kw, lab in KEYWORDS:
        if kw in low and lab not in f:
            f.append(lab)
    return f


def annotate(cands: list[dict], as_of: str | None = None) -> int:
    base = dt.date.fromisoformat(as_of) if as_of else dt.date.today()
    cutoff = (base - dt.timedelta(days=WINDOW_DAYS)).isoformat()
    n_flag = 0
    for c in cands:
        if not c.get("cuosha_score"):
            continue
        items = [(d, t, u) for (d, t, u) in _titles(c["code"]) if d >= cutoff][:MAX_ITEMS]
        if not items:
            continue
        news, labels = [], []
        for d, t, u in items:
            f = flags_in(t)
            news.append({"d": d, "t": t[:90], "u": u, "f": f})
            for x in f:
                if x not in labels:
                    labels.append(x)
        c["news"] = news
        c["news_flags"] = labels
        if labels:
            n_flag += 1
    return n_flag
