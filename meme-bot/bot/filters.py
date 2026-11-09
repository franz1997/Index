"""Filtri di ingresso sulle coppie DexScreener."""
from __future__ import annotations

import time

from .config import Config


def _f(d: dict | None, *keys, default=0.0) -> float:
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    try:
        return float(cur) if cur is not None else default
    except (TypeError, ValueError):
        return default


def evaluate_pair(pair: dict, cfg: Config, now: float | None = None) -> tuple[bool, list[str]]:
    """Ritorna (ok, motivi_di_scarto)."""
    now = now or time.time()
    reasons: list[str] = []

    if pair.get("chainId") != cfg.chain:
        reasons.append("chain diversa")
    quote = ((pair.get("quoteToken") or {}).get("symbol") or "").upper()
    if cfg.allowed_quote_symbols and quote not in [q.upper() for q in cfg.allowed_quote_symbols]:
        reasons.append(f"quote {quote} non ammessa")

    price = _f(pair, "priceUsd")
    if price <= 0:
        reasons.append("prezzo assente")

    liq = _f(pair, "liquidity", "usd")
    if not cfg.min_liquidity_usd <= liq <= cfg.max_liquidity_usd:
        reasons.append(f"liquidità {liq:,.0f}$ fuori range")

    fdv = _f(pair, "fdv") or _f(pair, "marketCap")
    if not cfg.min_fdv_usd <= fdv <= cfg.max_fdv_usd:
        reasons.append(f"fdv {fdv:,.0f}$ fuori range")

    vol_h1 = _f(pair, "volume", "h1")
    if vol_h1 < cfg.min_volume_h1_usd:
        reasons.append(f"volume 1h {vol_h1:,.0f}$ basso")

    buys = _f(pair, "txns", "h1", "buys")
    sells = _f(pair, "txns", "h1", "sells")
    if buys + sells < cfg.min_txns_h1:
        reasons.append(f"poche transazioni 1h ({buys + sells:.0f})")
    ratio = buys / sells if sells else (buys if buys else 0)
    if ratio < cfg.min_buy_sell_ratio_h1:
        reasons.append(f"rapporto buy/sell {ratio:.2f} basso")

    created = pair.get("pairCreatedAt")
    if created:
        age_min = (now - created / 1000) / 60
        if not cfg.min_age_minutes <= age_min <= cfg.max_age_minutes:
            reasons.append(f"età {age_min:.0f}min fuori range")
    else:
        reasons.append("età sconosciuta")

    ch1 = _f(pair, "priceChange", "h1")
    if not cfg.min_price_change_h1 <= ch1 <= cfg.max_price_change_h1:
        reasons.append(f"variazione 1h {ch1:.1f}% fuori range")
    ch5 = _f(pair, "priceChange", "m5")
    if ch5 > cfg.max_price_change_m5:
        reasons.append(f"variazione 5m {ch5:.1f}% troppo alta")

    return (not reasons, reasons)


def score_pair(pair: dict) -> float:
    """Punteggio semplice per ordinare i candidati validi (più alto = meglio)."""
    vol = _f(pair, "volume", "h1")
    liq = _f(pair, "liquidity", "usd") or 1
    buys = _f(pair, "txns", "h1", "buys")
    sells = _f(pair, "txns", "h1", "sells") or 1
    return (vol / liq) * min(buys / sells, 3.0)
