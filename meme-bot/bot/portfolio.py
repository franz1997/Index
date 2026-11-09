"""Posizioni aperte, storico operazioni e persistenza su disco."""
from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import asdict, dataclass, field


@dataclass
class Position:
    token: str
    symbol: str
    pair_address: str
    entry_price_usd: float
    cost_sol: float
    opened_at: float
    token_amount_raw: int = 0      # solo live: quantità di token in unità minime
    peak_price_usd: float = 0.0
    last_price_usd: float = 0.0

    def __post_init__(self):
        self.peak_price_usd = self.peak_price_usd or self.entry_price_usd
        self.last_price_usd = self.last_price_usd or self.entry_price_usd

    def pnl_pct(self, price: float | None = None) -> float:
        price = price if price is not None else self.last_price_usd
        return (price / self.entry_price_usd - 1) * 100


@dataclass
class Portfolio:
    path: str
    balance_sol: float = 0.0                       # usato in paper
    positions: dict[str, Position] = field(default_factory=dict)
    traded_at: dict[str, float] = field(default_factory=dict)   # token -> ultimo acquisto
    realized: list[dict] = field(default_factory=list)          # [{ts, pnl_sol}]

    @classmethod
    def load(cls, state_dir: str, start_balance: float) -> "Portfolio":
        os.makedirs(state_dir, exist_ok=True)
        path = os.path.join(state_dir, "portfolio.json")
        if not os.path.exists(path):
            return cls(path=path, balance_sol=start_balance)
        with open(path) as fh:
            d = json.load(fh)
        return cls(
            path=path,
            balance_sol=d.get("balance_sol", start_balance),
            positions={k: Position(**v) for k, v in d.get("positions", {}).items()},
            traded_at=d.get("traded_at", {}),
            realized=d.get("realized", []),
        )

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({
                "balance_sol": self.balance_sol,
                "positions": {k: asdict(v) for k, v in self.positions.items()},
                "traded_at": self.traded_at,
                "realized": self.realized[-1000:],
            }, fh, indent=2)
        os.replace(tmp, self.path)

    def in_cooldown(self, token: str, hours: float, now: float | None = None) -> bool:
        ts = self.traded_at.get(token)
        return ts is not None and (now or time.time()) - ts < hours * 3600

    def loss_last_24h(self, now: float | None = None) -> float:
        now = now or time.time()
        pnl = sum(r["pnl_sol"] for r in self.realized if now - r["ts"] < 86400)
        return -pnl if pnl < 0 else 0.0

    def log_trade(self, side: str, pos: Position, sol: float, price: float,
                  reason: str = "", pnl_sol: float | None = None) -> None:
        log_path = os.path.join(os.path.dirname(self.path), "trades.csv")
        new = not os.path.exists(log_path)
        with open(log_path, "a", newline="") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["time", "side", "symbol", "token", "sol", "price_usd", "pnl_sol", "reason"])
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), side, pos.symbol, pos.token,
                        f"{sol:.6f}", f"{price:.10g}",
                        "" if pnl_sol is None else f"{pnl_sol:.6f}", reason])
