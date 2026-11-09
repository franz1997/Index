"""Configurazione letta da variabili d'ambiente (file .env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


def _cast(value: str, typ):
    if typ is bool:
        return value.strip().lower() in ("1", "true", "yes", "si", "on")
    if typ is list:
        return [v.strip() for v in value.split(",") if v.strip()]
    return typ(value)


_TYPES = {"str": str, "int": int, "float": float, "bool": bool, "list": list}


@dataclass
class Config:
    # --- generale ---
    mode: str = "paper"              # "paper" (simulato) oppure "live" (soldi veri)
    chain: str = "solana"            # in live è supportata solo solana
    scan_interval_sec: int = 30
    state_dir: str = "state"

    # --- filtri di ingresso (DexScreener) ---
    min_liquidity_usd: float = 20_000
    max_liquidity_usd: float = 2_000_000
    min_fdv_usd: float = 50_000
    max_fdv_usd: float = 10_000_000
    min_volume_h1_usd: float = 10_000
    min_txns_h1: int = 100
    min_buy_sell_ratio_h1: float = 1.1   # acquisti/vendite nell'ultima ora
    min_age_minutes: float = 15
    max_age_minutes: float = 24 * 60
    min_price_change_h1: float = 0.0     # deve essere in salita...
    max_price_change_h1: float = 300.0   # ...ma non già esploso
    max_price_change_m5: float = 40.0    # evita di comprare in cima a una candela
    allowed_quote_symbols: list = field(default_factory=lambda: ["SOL", "WSOL"])

    # --- sicurezza ---
    use_rugcheck: bool = True
    max_rugcheck_score: float = 30.0     # score_normalised RugCheck (0 = ok, alto = rischioso)

    # --- gestione rischio / dimensione ---
    trade_size_sol: float = 0.05
    max_open_positions: int = 3
    max_daily_loss_sol: float = 0.3
    paper_start_balance_sol: float = 1.0
    paper_fee_pct: float = 1.5           # slippage + fee simulati per lato
    cooldown_hours: float = 24           # non ricomprare lo stesso token per N ore

    # --- uscite ---
    take_profit_pct: float = 60.0
    stop_loss_pct: float = 20.0
    trailing_activation_pct: float = 25.0
    trailing_stop_pct: float = 15.0
    max_hold_minutes: float = 180
    min_exit_liquidity_usd: float = 5_000   # se la liquidità crolla, esci subito

    # --- live (Solana / Jupiter) ---
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    wallet_private_key: str = ""          # base58, MAI committare
    jupiter_api_url: str = "https://lite-api.jup.ag/swap/v1"
    jupiter_api_key: str = ""
    slippage_bps: int = 300
    priority_fee_lamports: int = 200_000

    @classmethod
    def from_env(cls, env_file: str | None = ".env") -> "Config":
        if load_dotenv and env_file:
            load_dotenv(env_file)
        cfg = cls()
        for f in fields(cls):
            raw = os.getenv("BOT_" + f.name.upper())
            if raw is None or raw == "":
                continue
            typ = _TYPES[str(f.type)]
            setattr(cfg, f.name, _cast(raw, typ))
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.mode not in ("paper", "live"):
            raise ValueError("BOT_MODE deve essere 'paper' o 'live'")
        if self.mode == "live":
            if self.chain != "solana":
                raise ValueError("La modalità live supporta solo BOT_CHAIN=solana")
            if not self.wallet_private_key:
                raise ValueError("BOT_WALLET_PRIVATE_KEY mancante per la modalità live")
        if self.trade_size_sol <= 0 or self.stop_loss_pct <= 0:
            raise ValueError("trade_size_sol e stop_loss_pct devono essere > 0")
