"""Client minimale per le API pubbliche di DexScreener (https://docs.dexscreener.com)."""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)
BASE = "https://api.dexscreener.com"


class DexScreener:
    def __init__(self, session: requests.Session | None = None, timeout: float = 15):
        self.s = session or requests.Session()
        self.s.headers.setdefault("User-Agent", "meme-bot/1.0")
        self.timeout = timeout

    def _get(self, path: str):
        for attempt in range(3):
            try:
                r = self.s.get(BASE + path, timeout=self.timeout)
                if r.status_code == 429:  # rate limit
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                return r.json()
            except requests.RequestException as e:
                log.warning("DexScreener %s errore: %s", path, e)
                time.sleep(1 + attempt)
        return None

    # --- sorgenti di token "nuovi / caldi" ---
    def latest_profiles(self) -> list[dict]:
        return self._get("/token-profiles/latest/v1") or []

    def latest_boosts(self) -> list[dict]:
        return self._get("/token-boosts/latest/v1") or []

    def top_boosts(self) -> list[dict]:
        return self._get("/token-boosts/top/v1") or []

    def candidate_tokens(self, chain: str) -> list[str]:
        """Indirizzi unici dei token della chain scelta presi da profili e boost."""
        seen: dict[str, None] = {}
        for src in (self.latest_profiles(), self.latest_boosts(), self.top_boosts()):
            for item in src if isinstance(src, list) else []:
                if item.get("chainId") == chain and item.get("tokenAddress"):
                    seen.setdefault(item["tokenAddress"], None)
        return list(seen)

    # --- dati di mercato ---
    def pairs_for_tokens(self, chain: str, addresses: list[str]) -> list[dict]:
        out: list[dict] = []
        for i in range(0, len(addresses), 30):  # max 30 indirizzi per chiamata
            chunk = ",".join(addresses[i:i + 30])
            data = self._get(f"/tokens/v1/{chain}/{chunk}")
            if isinstance(data, list):
                out.extend(data)
        return out

    def best_pairs(self, chain: str, addresses: list[str]) -> dict[str, dict]:
        """Per ogni token restituisce la coppia con più liquidità."""
        best: dict[str, dict] = {}
        for p in self.pairs_for_tokens(chain, addresses):
            addr = (p.get("baseToken") or {}).get("address")
            if not addr:
                continue
            liq = (p.get("liquidity") or {}).get("usd") or 0
            if addr not in best or liq > ((best[addr].get("liquidity") or {}).get("usd") or 0):
                best[addr] = p
        return best
