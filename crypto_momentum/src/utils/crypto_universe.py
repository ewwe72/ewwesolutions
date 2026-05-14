"""Tradable crypto universe.

Alpaca's USD-quoted spot crypto offering, restricted to names with enough
history and dollar volume for a momentum signal to be meaningful. The
list is intentionally conservative — Alpaca lists more pairs than this,
but the long-tail names have spreads wide enough to eat the strategy's
expected alpha.

Alpaca-native symbols use the BASE/QUOTE format with a slash (e.g.
``BTC/USD``). yfinance uses BASE-QUOTE with a dash (e.g. ``BTC-USD``).
Both forms are provided here; the backtest reads from yfinance and the
live driver reads from Alpaca, so each call site picks the right form.

Survivorship note: this list reflects what trades on Alpaca *now*.
Coins that delisted or rugged (LUNA, FTT, etc.) are absent. Crypto's
survivorship bias is harder to quantify than equities' (no S&P-style
canonical "all listings ever" list), but it exists; treat backtest
returns as upper-bound estimates of forward returns.
"""

# Alpaca trading symbols (live driver)
ALPACA_SYMBOLS: tuple[str, ...] = (
    "BTC/USD",
    "ETH/USD",
    "SOL/USD",
    "AVAX/USD",
    "LINK/USD",
    "DOT/USD",
    "AAVE/USD",
    "UNI/USD",
    "LTC/USD",
    "BCH/USD",
    "DOGE/USD",
    "SHIB/USD",
    "XRP/USD",
    "MKR/USD",
    "GRT/USD",
    "YFI/USD",
)


# Mapping from Alpaca format to yfinance format. Used by the backtest.
ALPACA_TO_YFINANCE: dict[str, str] = {
    "BTC/USD": "BTC-USD",
    "ETH/USD": "ETH-USD",
    "SOL/USD": "SOL-USD",
    "AVAX/USD": "AVAX-USD",
    "LINK/USD": "LINK-USD",
    "DOT/USD": "DOT-USD",
    "AAVE/USD": "AAVE-USD",
    "UNI/USD": "UNI7083-USD",  # yfinance: UNI clashes with another ticker
    "LTC/USD": "LTC-USD",
    "BCH/USD": "BCH-USD",
    "DOGE/USD": "DOGE-USD",
    "SHIB/USD": "SHIB-USD",
    "XRP/USD": "XRP-USD",
    "MKR/USD": "MKR-USD",
    "GRT/USD": "GRT-USD",
    "YFI/USD": "YFI-USD",
}


YFINANCE_SYMBOLS: tuple[str, ...] = tuple(ALPACA_TO_YFINANCE.values())
