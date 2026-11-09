"""Loop principale del bot."""
from __future__ import annotations

import argparse
import logging
import os
import signal
import time

from .config import Config
from .dexscreener import DexScreener
from .executors import PaperExecutor, SolanaExecutor
from .filters import evaluate_pair, score_pair
from .portfolio import Portfolio, Position
from .safety import RugCheck
from .strategy import exit_reason

log = logging.getLogger("meme-bot")


class Bot:
    def __init__(self, cfg: Config, dex: DexScreener | None = None,
                 rug: RugCheck | None = None, executor=None, portfolio: Portfolio | None = None):
        self.cfg = cfg
        self.dex = dex or DexScreener()
        self.rug = rug if rug is not None else (RugCheck(cfg.max_rugcheck_score)
                                                if cfg.use_rugcheck and cfg.chain == "solana" else None)
        self.pf = portfolio or Portfolio.load(cfg.state_dir, cfg.paper_start_balance_sol)
        self.ex = executor or (SolanaExecutor(cfg) if cfg.mode == "live" else PaperExecutor(cfg, self.pf))
        self.running = True

    # ---------- gestione posizioni aperte ----------
    def manage_positions(self, now: float | None = None) -> None:
        if not self.pf.positions:
            return
        pairs = self.dex.best_pairs(self.cfg.chain, list(self.pf.positions))
        for token, pos in list(self.pf.positions.items()):
            pair = pairs.get(token)
            if not pair or not pair.get("priceUsd"):
                log.warning("%s: nessun dato di prezzo, riprovo al prossimo giro", pos.symbol)
                continue
            price = float(pair["priceUsd"])
            liq = (pair.get("liquidity") or {}).get("usd")
            reason = exit_reason(pos, price, liq, self.cfg, now)
            log.info("  %-10s %+7.1f%%  (picco %+.1f%%)", pos.symbol, pos.pnl_pct(price),
                     (pos.peak_price_usd / pos.entry_price_usd - 1) * 100)
            if reason:
                self.close(pos, price, reason, now)
        self.pf.save()

    def close(self, pos: Position, price: float, reason: str, now: float | None = None) -> None:
        fill = self.ex.sell(pos, price)
        if fill is None:
            return  # riprova al prossimo ciclo
        pnl = fill.sol - pos.cost_sol
        log.info("VENDO %s: %s -> %.4f SOL (PnL %+.4f SOL)", pos.symbol, reason, fill.sol, pnl)
        self.pf.realized.append({"ts": now or time.time(), "pnl_sol": pnl})
        self.pf.log_trade("SELL", pos, fill.sol, price, reason, pnl)
        del self.pf.positions[pos.token]

    # ---------- ricerca nuove opportunità ----------
    def find_candidates(self, now: float | None = None, verbose: bool = False) -> list[dict]:
        tokens = [t for t in self.dex.candidate_tokens(self.cfg.chain)
                  if t not in self.pf.positions and not self.pf.in_cooldown(t, self.cfg.cooldown_hours, now)]
        if not tokens:
            return []
        good = []
        for token, pair in self.dex.best_pairs(self.cfg.chain, tokens).items():
            ok, reasons = evaluate_pair(pair, self.cfg, now)
            sym = (pair.get("baseToken") or {}).get("symbol", "?")
            if ok:
                good.append(pair)
            elif verbose:
                log.info("  scarto %-10s %s", sym, "; ".join(reasons))
        good.sort(key=score_pair, reverse=True)
        return good

    def can_open(self) -> bool:
        if len(self.pf.positions) >= self.cfg.max_open_positions:
            return False
        if self.pf.loss_last_24h() >= self.cfg.max_daily_loss_sol:
            log.warning("Limite di perdita giornaliera raggiunto: niente nuovi acquisti")
            return False
        return True

    def open_new(self, now: float | None = None) -> None:
        if not self.can_open():
            return
        for pair in self.find_candidates(now):
            if not self.can_open():
                break
            token = pair["baseToken"]["address"]
            sym = pair["baseToken"].get("symbol", "?")
            if self.ex.balance_sol() < self.cfg.trade_size_sol * 1.05:
                log.warning("Saldo insufficiente per nuovi acquisti")
                break
            if self.rug:
                ok, why = self.rug.check(token)
                if not ok:
                    log.info("  %s scartato da RugCheck: %s", sym, why)
                    self.pf.traded_at[token] = now or time.time()  # non ricontrollarlo subito
                    continue
            ok, why = self.ex.pre_trade_check(token, self.cfg.trade_size_sol)
            if not ok:
                log.info("  %s scartato: %s", sym, why)
                continue
            price = float(pair["priceUsd"])
            fill = self.ex.buy(token, price, self.cfg.trade_size_sol)
            if not fill:
                continue
            pos = Position(token=token, symbol=sym, pair_address=pair.get("pairAddress", ""),
                           entry_price_usd=fill.price_usd, cost_sol=fill.sol,
                           opened_at=now or time.time(), token_amount_raw=fill.token_amount_raw)
            self.pf.positions[token] = pos
            self.pf.traded_at[token] = pos.opened_at
            self.pf.log_trade("BUY", pos, fill.sol, price, pair.get("url", ""))
            log.info("COMPRO %s a %.10g$ per %.4f SOL  %s", sym, price, fill.sol, pair.get("url", ""))
        self.pf.save()

    def cycle(self, now: float | None = None) -> None:
        log.info("--- ciclo [%s] saldo %.4f SOL, posizioni %d ---",
                 self.cfg.mode, self.ex.balance_sol(), len(self.pf.positions))
        self.manage_positions(now)
        self.open_new(now)

    def run(self) -> None:
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "running", False))
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        while self.running:
            try:
                self.cycle()
            except Exception:  # noqa: BLE001
                log.exception("Errore nel ciclo")
            for _ in range(self.cfg.scan_interval_sec):
                if not self.running:
                    break
                time.sleep(1)
        log.info("Bot fermato. Le posizioni aperte restano salvate in %s", self.pf.path)


def main() -> None:
    ap = argparse.ArgumentParser(description="Meme coin bot DexScreener")
    ap.add_argument("--env", default=".env", help="file di configurazione")
    ap.add_argument("--once", action="store_true", help="esegui un solo ciclo")
    ap.add_argument("--scan", action="store_true", help="mostra solo i candidati, nessun trade")
    ap.add_argument("--status", action="store_true", help="mostra posizioni e PnL")
    args = ap.parse_args()

    cfg = Config.from_env(args.env)
    os.makedirs(cfg.state_dir, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(os.path.join(cfg.state_dir, "bot.log"))])

    if cfg.mode == "live":
        log.warning("MODALITÀ LIVE: verranno usati fondi reali dal wallet configurato!")

    if args.status:
        pf = Portfolio.load(cfg.state_dir, cfg.paper_start_balance_sol)
        total = sum(r["pnl_sol"] for r in pf.realized)
        print(f"Saldo paper: {pf.balance_sol:.4f} SOL | PnL realizzato: {total:+.4f} SOL "
              f"su {len(pf.realized)} trade")
        for p in pf.positions.values():
            print(f"  {p.symbol:10s} costo {p.cost_sol:.4f} SOL  ultimo {p.pnl_pct():+.1f}%")
        return

    bot = Bot(cfg)
    if args.scan:
        for p in bot.find_candidates(verbose=True):
            b = p["baseToken"]
            print(f"{b.get('symbol', '?'):10s} liq {p['liquidity']['usd']:>10,.0f}$  "
                  f"vol1h {p['volume']['h1']:>10,.0f}$  1h {p['priceChange'].get('h1', 0):+6.1f}%  "
                  f"{p.get('url', '')}")
        return
    if args.once:
        bot.cycle()
    else:
        bot.run()


if __name__ == "__main__":
    main()
