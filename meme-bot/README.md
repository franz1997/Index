# Meme Bot – DexScreener → compra/vendi automatico (Solana)

Il bot cerca meme coin "calde" su **DexScreener**, le filtra, controlla che non siano truffe
evidenti e poi **compra e rivende in automatico** con take profit, stop loss e trailing stop.

> ⚠️ **Leggi prima di usarlo.** Le meme coin sono estremamente rischiose: la maggior parte va a zero,
> molte sono rug pull o honeypot, e ci sono bot (sniper/MEV) molto più veloci di questo.
> Nessun filtro garantisce profitti. Parti in **modalità paper** (simulata, di default),
> poi eventualmente in live con un **wallet dedicato** e cifre che puoi permetterti di perdere.
> Questo non è un consiglio finanziario.

## Come funziona

Ogni `BOT_SCAN_INTERVAL_SEC` secondi:

1. **Gestisce le posizioni aperte**: legge il prezzo da DexScreener e vende se scatta una regola:
   - stop loss (default −20%)
   - take profit (default +60%)
   - trailing stop: dopo +25% vende se scende del 15% dal picco
   - tempo massimo di permanenza (default 180 min)
   - liquidità crollata sotto 5.000$ (probabile rug)
2. **Cerca nuovi token** dalle API DexScreener (token profile recenti + token boostati) e li filtra:
   liquidità, FDV, volume 1h, numero di transazioni, rapporto acquisti/vendite, età della coppia,
   variazione di prezzo 1h/5m (in salita ma non già esploso), coppia contro SOL.
3. **Controlli di sicurezza** prima di comprare:
   - **RugCheck** (`api.rugcheck.xyz`): scarta token con rischi "danger" (mint/freeze authority,
     LP non bloccata, ecc.) o score troppo alto.
   - **Test anti-honeypot** (solo live): chiede a Jupiter una quotazione di acquisto e subito di
     rivendita; se il giro perde più del 15% il token viene scartato.
4. **Compra** con Jupiter (swap SOL → token) fino a `BOT_MAX_OPEN_POSITIONS` posizioni.

Limiti di rischio: dimensione fissa per trade, numero massimo di posizioni, **stop giornaliero**
(se in 24h perdi più di `BOT_MAX_DAILY_LOSS_SOL` il bot smette di comprare), cooldown per non
ricomprare lo stesso token.

## Installazione

```bash
cd meme-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # poi modifica i parametri
```

## Uso

```bash
python -m bot.main --scan     # mostra i candidati attuali (e perché gli altri sono scartati), nessun trade
python -m bot.main --once     # un solo ciclo
python -m bot.main            # loop continuo (Ctrl+C per fermare)
python -m bot.main --status   # saldo, PnL realizzato, posizioni aperte
```

Tutto viene salvato in `state/`:
- `portfolio.json` – posizioni aperte e saldo paper (il bot riprende da qui se lo riavvii)
- `trades.csv` – storico di tutti gli acquisti/vendite con PnL
- `bot.log` – log

## Passare in modalità LIVE

1. Crea un **wallet Solana nuovo** solo per il bot (es. Phantom → nuovo account) e mettici poco SOL
   (trade size + ~0.05 SOL per le fee).
2. Esporta la chiave privata (base58) e mettila in `.env` → `BOT_WALLET_PRIVATE_KEY=...`
   (`.env` è in `.gitignore`: **non committarlo mai**, non condividerlo).
3. Usa un RPC affidabile (Helius, QuickNode, Triton…): quello pubblico è lento e limitato.
   `BOT_SOLANA_RPC_URL=https://...`
4. Jupiter: di default usa `lite-api.jup.ag` (gratis, con limiti). Con una chiave API di Jupiter
   imposta `BOT_JUPITER_API_URL=https://api.jup.ag/swap/v1` e `BOT_JUPITER_API_KEY=...`.
5. `BOT_MODE=live` e avvia. Ogni transazione viene loggata con il link a Solscan.

## Parametri principali (`.env`)

| Variabile | Default | Significato |
|---|---|---|
| `BOT_MODE` | `paper` | `paper` simulato, `live` soldi veri |
| `BOT_TRADE_SIZE_SOL` | 0.05 | SOL spesi per ogni acquisto |
| `BOT_MAX_OPEN_POSITIONS` | 3 | posizioni contemporanee |
| `BOT_MAX_DAILY_LOSS_SOL` | 0.3 | stop acquisti dopo questa perdita in 24h |
| `BOT_MIN_LIQUIDITY_USD` | 20000 | liquidità minima della coppia |
| `BOT_MIN_VOLUME_H1_USD` | 10000 | volume minimo ultima ora |
| `BOT_MIN_BUY_SELL_RATIO_H1` | 1.1 | acquisti/vendite minimo ultima ora |
| `BOT_MIN_AGE_MINUTES` / `BOT_MAX_AGE_MINUTES` | 15 / 1440 | età della coppia |
| `BOT_TAKE_PROFIT_PCT` | 60 | take profit % |
| `BOT_STOP_LOSS_PCT` | 20 | stop loss % |
| `BOT_TRAILING_ACTIVATION_PCT` / `BOT_TRAILING_STOP_PCT` | 25 / 15 | trailing stop |
| `BOT_MAX_HOLD_MINUTES` | 180 | durata massima di una posizione |
| `BOT_SLIPPAGE_BPS` | 300 | slippage massimo Jupiter (300 = 3%) |
| `BOT_PAPER_FEE_PCT` | 1.5 | fee+slippage simulati per lato in paper |

L'elenco completo è in `bot/config.py` (ogni campo diventa `BOT_<NOME_MAIUSCOLO>`).

## Limiti noti

- Il prezzo per le uscite viene da DexScreener (aggiornamento ogni pochi secondi): in un crollo
  improvviso lo stop loss può essere eseguito molto sotto la soglia.
- La simulazione paper è ottimista: non modella fallimenti di transazione, MEV e slippage reale.
- Live supporta solo Solana (swap via Jupiter). Lo scanner/paper funziona anche con altre chain
  (`BOT_CHAIN=base`, `bsc`, …) impostando `BOT_ALLOWED_QUOTE_SYMBOLS` (es. `WETH`).

## Test

```bash
pip install pytest && python -m pytest -q
```
