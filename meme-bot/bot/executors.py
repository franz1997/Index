"""Esecuzione degli ordini: PaperExecutor (simulato) e SolanaExecutor (Jupiter, soldi veri)."""
from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass

import requests

from .config import Config
from .portfolio import Portfolio, Position

log = logging.getLogger(__name__)
SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS = 1_000_000_000


@dataclass
class Fill:
    sol: float                 # SOL spesi (buy) o ricevuti (sell)
    price_usd: float           # prezzo di ingresso/uscita effettivo stimato
    token_amount_raw: int = 0


class PaperExecutor:
    """Simula gli ordini al prezzo DexScreener applicando fee/slippage."""

    def __init__(self, cfg: Config, portfolio: Portfolio):
        self.cfg, self.pf = cfg, portfolio

    def balance_sol(self) -> float:
        return self.pf.balance_sol

    def buy(self, token: str, price_usd: float, sol: float) -> Fill | None:
        if sol > self.pf.balance_sol:
            return None
        self.pf.balance_sol -= sol
        return Fill(sol=sol, price_usd=price_usd * (1 + self.cfg.paper_fee_pct / 100))

    def sell(self, pos: Position, price_usd: float) -> Fill | None:
        out = pos.cost_sol * (price_usd / pos.entry_price_usd) * (1 - self.cfg.paper_fee_pct / 100)
        self.pf.balance_sol += out
        return Fill(sol=out, price_usd=price_usd)

    def pre_trade_check(self, token: str, sol: float) -> tuple[bool, str]:
        return True, "paper"


class SolanaExecutor:
    """Swap reali SOL <-> token tramite Jupiter, firmati col wallet locale."""

    def __init__(self, cfg: Config):
        from solders.keypair import Keypair  # import lazy: serve solo in live

        self.cfg = cfg
        self.kp = Keypair.from_base58_string(cfg.wallet_private_key.strip())
        self.pubkey = str(self.kp.pubkey())
        self.s = requests.Session()
        if cfg.jupiter_api_key:
            self.s.headers["x-api-key"] = cfg.jupiter_api_key
        log.info("Wallet live: %s", self.pubkey)

    # ---------- RPC ----------
    def _rpc(self, method: str, params: list):
        r = self.s.post(self.cfg.solana_rpc_url, timeout=30,
                        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"RPC {method}: {data['error']}")
        return data["result"]

    def balance_sol(self) -> float:
        return self._rpc("getBalance", [self.pubkey, {"commitment": "confirmed"}])["value"] / LAMPORTS

    def token_balance_raw(self, mint: str) -> int:
        res = self._rpc("getTokenAccountsByOwner",
                        [self.pubkey, {"mint": mint}, {"encoding": "jsonParsed", "commitment": "confirmed"}])
        return sum(int(a["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"])
                   for a in res.get("value", []))

    # ---------- Jupiter ----------
    def _quote(self, input_mint: str, output_mint: str, amount: int) -> dict:
        r = self.s.get(f"{self.cfg.jupiter_api_url}/quote", timeout=20, params={
            "inputMint": input_mint, "outputMint": output_mint,
            "amount": str(amount), "slippageBps": self.cfg.slippage_bps,
        })
        r.raise_for_status()
        return r.json()

    def _swap(self, quote: dict) -> str:
        r = self.s.post(f"{self.cfg.jupiter_api_url}/swap", timeout=30, json={
            "quoteResponse": quote,
            "userPublicKey": self.pubkey,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": {"priorityLevelWithMaxLamports": {
                "maxLamports": self.cfg.priority_fee_lamports, "priorityLevel": "veryHigh"}},
        })
        r.raise_for_status()
        return self._sign_and_send(r.json()["swapTransaction"])

    def _sign_and_send(self, tx_b64: str) -> str:
        from solders.transaction import VersionedTransaction

        raw = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
        signed = VersionedTransaction(raw.message, [self.kp])
        sig = self._rpc("sendTransaction", [base64.b64encode(bytes(signed)).decode(),
                                            {"encoding": "base64", "skipPreflight": True, "maxRetries": 3}])
        self._confirm(sig)
        return sig

    def _confirm(self, sig: str, timeout: float = 60) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = self._rpc("getSignatureStatuses", [[sig]])["value"][0]
            if st:
                if st.get("err"):
                    raise RuntimeError(f"transazione fallita {sig}: {st['err']}")
                if st.get("confirmationStatus") in ("confirmed", "finalized"):
                    return
            time.sleep(2)
        raise TimeoutError(f"transazione non confermata: {sig}")

    # ---------- interfaccia ----------
    def pre_trade_check(self, token: str, sol: float) -> tuple[bool, str]:
        """Anti-honeypot: verifica che si possa rivendere subito senza perdite assurde."""
        try:
            lamports = int(sol * LAMPORTS)
            q_buy = self._quote(SOL_MINT, token, lamports)
            q_sell = self._quote(token, SOL_MINT, int(q_buy["outAmount"]))
            loss = (1 - int(q_sell["outAmount"]) / lamports) * 100
        except Exception as e:  # noqa: BLE001
            return False, f"quote non disponibile: {e}"
        if loss > 15:
            return False, f"round-trip perde {loss:.1f}% (possibile honeypot/tassa)"
        return True, f"round-trip {loss:.1f}%"

    def buy(self, token: str, price_usd: float, sol: float) -> Fill | None:
        try:
            before = self.token_balance_raw(token)
            quote = self._quote(SOL_MINT, token, int(sol * LAMPORTS))
            sig = self._swap(quote)
            log.info("BUY tx https://solscan.io/tx/%s", sig)
            received = 0
            for _ in range(10):
                received = self.token_balance_raw(token) - before
                if received > 0:
                    break
                time.sleep(2)
            received = received or int(quote["outAmount"])
            return Fill(sol=sol, price_usd=price_usd, token_amount_raw=received)
        except Exception as e:  # noqa: BLE001
            log.error("Acquisto fallito %s: %s", token, e)
            return None

    def sell(self, pos: Position, price_usd: float) -> Fill | None:
        try:
            amount = self.token_balance_raw(pos.token)
            if amount <= 0:
                log.warning("Nessun saldo di %s da vendere", pos.symbol)
                return Fill(sol=0.0, price_usd=price_usd)
            sol_before = self.balance_sol()
            sig = self._swap(self._quote(pos.token, SOL_MINT, amount))
            log.info("SELL tx https://solscan.io/tx/%s", sig)
            return Fill(sol=max(self.balance_sol() - sol_before, 0.0), price_usd=price_usd)
        except Exception as e:  # noqa: BLE001
            log.error("Vendita fallita %s: %s", pos.symbol, e)
            return None
