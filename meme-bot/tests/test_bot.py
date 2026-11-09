import time

import pytest

from bot.config import Config
from bot.filters import evaluate_pair
from bot.main import Bot
from bot.portfolio import Portfolio, Position
from bot.safety import RugCheck
from bot.strategy import exit_reason

NOW = 1_800_000_000.0


def make_pair(addr="TOKEN1", price=0.001, liq=50_000, age_min=60, **over):
    p = {
        "chainId": "solana", "dexId": "raydium", "pairAddress": "PAIR_" + addr,
        "url": "https://dexscreener.com/solana/pair_" + addr.lower(),
        "baseToken": {"address": addr, "symbol": addr[:5], "name": addr},
        "quoteToken": {"address": "So111", "symbol": "SOL"},
        "priceUsd": str(price),
        "txns": {"h1": {"buys": 300, "sells": 200}, "m5": {"buys": 30, "sells": 20}},
        "volume": {"h1": 80_000, "h24": 500_000},
        "priceChange": {"m5": 3.0, "h1": 25.0},
        "liquidity": {"usd": liq},
        "fdv": 800_000,
        "pairCreatedAt": int((NOW - age_min * 60) * 1000),
    }
    p.update(over)
    return p


class FakeDex:
    def __init__(self, pairs):
        self.pairs = {p["baseToken"]["address"]: p for p in pairs}

    def candidate_tokens(self, chain):
        return list(self.pairs)

    def best_pairs(self, chain, addresses):
        return {a: self.pairs[a] for a in addresses if a in self.pairs}


def cfg(tmp_path, **kw):
    c = Config(state_dir=str(tmp_path), use_rugcheck=False, **kw)
    c.validate()
    return c


def test_good_pair_passes(tmp_path):
    ok, reasons = evaluate_pair(make_pair(), cfg(tmp_path), NOW)
    assert ok, reasons


@pytest.mark.parametrize("over,needle", [
    ({"liquidity": {"usd": 1000}}, "liquidità"),
    ({"txns": {"h1": {"buys": 20, "sells": 30}}}, "transazioni"),
    ({"txns": {"h1": {"buys": 100, "sells": 200}}}, "buy/sell"),
    ({"priceChange": {"h1": 900, "m5": 1}}, "variazione 1h"),
    ({"quoteToken": {"symbol": "USDC"}}, "quote"),
    ({"pairCreatedAt": int((NOW - 60) * 1000)}, "età"),
])
def test_bad_pairs_rejected(tmp_path, over, needle):
    ok, reasons = evaluate_pair(make_pair(**over), cfg(tmp_path), NOW)
    assert not ok and any(needle in r for r in reasons), reasons


def _pos(price=1.0):
    return Position(token="T", symbol="T", pair_address="P", entry_price_usd=price,
                    cost_sol=0.1, opened_at=NOW)


def test_exit_rules(tmp_path):
    c = cfg(tmp_path)
    assert exit_reason(_pos(), 0.79, 50_000, c, NOW).startswith("stop loss")
    assert exit_reason(_pos(), 1.61, 50_000, c, NOW).startswith("take profit")
    assert exit_reason(_pos(), 1.05, 50_000, c, NOW) is None
    assert exit_reason(_pos(), 1.0, 50_000, c, NOW + 4 * 3600).startswith("tempo")
    assert exit_reason(_pos(), 1.0, 100, c, NOW).startswith("liquidità")
    p = _pos()
    assert exit_reason(p, 1.4, 50_000, c, NOW) is None       # picco +40%
    assert exit_reason(p, 1.15, 50_000, c, NOW).startswith("trailing")  # -17.8% dal picco


def test_rugcheck_evaluate():
    rc = RugCheck(max_score=30)
    assert rc.evaluate({"score_normalised": 5, "risks": []})[0]
    assert not rc.evaluate({"score_normalised": 80, "risks": []})[0]
    assert not rc.evaluate({"score_normalised": 1, "risks": [{"name": "Mint authority", "level": "danger"}]})[0]


def test_full_paper_cycle(tmp_path):
    c = cfg(tmp_path, max_open_positions=2)
    pairs = [make_pair("AAA"), make_pair("BBB"), make_pair("CCC"),
             make_pair("BAD", liq=100)]
    dex = FakeDex(pairs)
    pf = Portfolio.load(c.state_dir, 1.0)
    bot = Bot(c, dex=dex, rug=None, portfolio=pf)

    bot.cycle(NOW)
    assert len(pf.positions) == 2 and "BAD" not in pf.positions
    assert pf.balance_sol == pytest.approx(0.9)

    # un token raddoppia -> take profit; l'altro crolla -> stop loss
    held = list(pf.positions)
    dex.pairs[held[0]]["priceUsd"] = "0.002"
    dex.pairs[held[1]]["priceUsd"] = "0.0005"
    bot.manage_positions(NOW + 60)
    assert not pf.positions
    assert len(pf.realized) == 2
    # persistenza
    again = Portfolio.load(c.state_dir, 1.0)
    assert again.balance_sol == pytest.approx(pf.balance_sol)
    assert (tmp_path / "trades.csv").read_text().count("\n") == 5  # header + 4 trade

    # cooldown: i token già tradati non vengono ricomprati
    bot.cycle(NOW + 120)
    assert set(pf.positions) == {"CCC"}


def test_daily_loss_limit_blocks_buys(tmp_path):
    c = cfg(tmp_path, max_daily_loss_sol=0.1)
    pf = Portfolio.load(c.state_dir, 1.0)
    pf.realized.append({"ts": time.time(), "pnl_sol": -0.2})
    bot = Bot(c, dex=FakeDex([make_pair()]), rug=None, portfolio=pf)
    bot.open_new(NOW)
    assert not pf.positions


def test_config_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_TRADE_SIZE_SOL", "0.2")
    monkeypatch.setenv("BOT_USE_RUGCHECK", "false")
    monkeypatch.setenv("BOT_ALLOWED_QUOTE_SYMBOLS", "SOL,USDC")
    c = Config.from_env(None)
    assert c.trade_size_sol == 0.2 and c.use_rugcheck is False
    assert c.allowed_quote_symbols == ["SOL", "USDC"]
    monkeypatch.setenv("BOT_MODE", "live")
    with pytest.raises(ValueError):
        Config.from_env(None)
