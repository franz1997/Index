"""Regole di uscita: take profit, stop loss, trailing stop, tempo massimo, liquidità."""
from __future__ import annotations

import time

from .config import Config
from .portfolio import Position


def exit_reason(pos: Position, price: float, liquidity_usd: float | None,
                cfg: Config, now: float | None = None) -> str | None:
    now = now or time.time()
    pos.last_price_usd = price
    pos.peak_price_usd = max(pos.peak_price_usd, price)

    pnl = pos.pnl_pct(price)
    peak_gain = (pos.peak_price_usd / pos.entry_price_usd - 1) * 100
    drawdown = (1 - price / pos.peak_price_usd) * 100

    if liquidity_usd is not None and liquidity_usd < cfg.min_exit_liquidity_usd:
        return f"liquidità crollata ({liquidity_usd:,.0f}$)"
    if pnl <= -cfg.stop_loss_pct:
        return f"stop loss ({pnl:.1f}%)"
    if pnl >= cfg.take_profit_pct:
        return f"take profit ({pnl:.1f}%)"
    if peak_gain >= cfg.trailing_activation_pct and drawdown >= cfg.trailing_stop_pct:
        return f"trailing stop (picco +{peak_gain:.1f}%, ora {pnl:+.1f}%)"
    if (now - pos.opened_at) / 60 >= cfg.max_hold_minutes:
        return f"tempo massimo ({pnl:+.1f}%)"
    return None
