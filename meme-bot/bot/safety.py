"""Controlli anti-rug tramite RugCheck (solo Solana)."""
from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)


class RugCheck:
    URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"

    def __init__(self, max_score: float, session: requests.Session | None = None):
        self.max_score = max_score
        self.s = session or requests.Session()

    def check(self, mint: str) -> tuple[bool, str]:
        try:
            r = self.s.get(self.URL.format(mint=mint), timeout=15)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            # in caso di dubbio NON compriamo
            return False, f"rugcheck non disponibile: {e}"
        return self.evaluate(data)

    def evaluate(self, data: dict) -> tuple[bool, str]:
        dangers = [r.get("name", "?") for r in data.get("risks") or [] if r.get("level") == "danger"]
        if dangers:
            return False, "rischi gravi: " + ", ".join(dangers)
        score = data.get("score_normalised", data.get("score"))
        if score is not None and float(score) > self.max_score:
            return False, f"score rugcheck {score} > {self.max_score}"
        return True, "ok"
