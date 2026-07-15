from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping


class MarketType(str, Enum):
    FOREX = "forex"
    CRYPTO_SPOT = "crypto_spot"
    CRYPTO_FUTURES = "crypto_futures"


FOREX_CURRENCIES = {
    "AUD",
    "CAD",
    "CHF",
    "EUR",
    "GBP",
    "JPY",
    "NZD",
    "USD",
}

CRYPTO_QUOTE_ASSETS = (
    "FDUSD",
    "USDT",
    "USDC",
    "BUSD",
    "TUSD",
    "DAI",
    "USD",
    "EUR",
    "TRY",
    "BTC",
    "ETH",
    "BNB",
)

STABLE_USD_QUOTES = {"USD", "USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI"}


def normalize_market_type(value: object | None, *, default: MarketType = MarketType.FOREX) -> MarketType:
    if isinstance(value, MarketType):
        return value
    text = str(value or default.value).strip().lower().replace("-", "_")
    aliases = {
        "fx": MarketType.FOREX,
        "forex": MarketType.FOREX,
        "crypto": MarketType.CRYPTO_SPOT,
        "spot": MarketType.CRYPTO_SPOT,
        "crypto_spot": MarketType.CRYPTO_SPOT,
        "futures": MarketType.CRYPTO_FUTURES,
        "future": MarketType.CRYPTO_FUTURES,
        "perp": MarketType.CRYPTO_FUTURES,
        "perpetual": MarketType.CRYPTO_FUTURES,
        "crypto_futures": MarketType.CRYPTO_FUTURES,
    }
    return aliases.get(text, default)


def is_crypto_market(market_type: object | None) -> bool:
    return normalize_market_type(market_type) in {MarketType.CRYPTO_SPOT, MarketType.CRYPTO_FUTURES}


def normalize_symbol(symbol: object) -> str:
    text = str(symbol or "").upper().strip()
    if ":" in text:
        text = text.split(":", 1)[0]
    for separator in ("/", "-", "_", " "):
        text = text.replace(separator, "")
    return text


def _split_separated_symbol(symbol: object) -> tuple[str, str] | None:
    text = str(symbol or "").upper().strip()
    if ":" in text:
        text = text.split(":", 1)[0]
    for separator in ("/", "-", "_"):
        if separator not in text:
            continue
        left, right = text.split(separator, 1)
        base = normalize_symbol(left)
        quote = normalize_symbol(right)
        if base and quote:
            return base, quote
    return None


def _split_crypto_symbol(clean: str, quote_assets: Iterable[str] | None = None) -> tuple[str, str] | None:
    quotes = sorted({normalize_symbol(item) for item in (quote_assets or CRYPTO_QUOTE_ASSETS)}, key=len, reverse=True)
    for quote in quotes:
        if not quote or not clean.endswith(quote) or len(clean) <= len(quote):
            continue
        return clean[: -len(quote)], quote
    return None


def split_symbol(
    symbol: object,
    *,
    market_type: object | None = None,
    quote_assets: Iterable[str] | None = None,
) -> tuple[str, str]:
    separated = _split_separated_symbol(symbol)
    if separated is not None:
        return separated

    clean = normalize_symbol(symbol)
    if not clean:
        raise ValueError("Empty symbol")

    requested_market = normalize_market_type(market_type, default=MarketType.FOREX) if market_type else None
    if requested_market == MarketType.FOREX:
        if len(clean) != 6:
            raise ValueError(f"Unsupported forex symbol: {symbol}")
        return clean[:3], clean[3:]

    crypto_parts = _split_crypto_symbol(clean, quote_assets)
    if requested_market in {MarketType.CRYPTO_SPOT, MarketType.CRYPTO_FUTURES}:
        if crypto_parts is None:
            raise ValueError(f"Unsupported crypto symbol: {symbol}")
        return crypto_parts

    if len(clean) == 6 and clean[:3] in FOREX_CURRENCIES and clean[3:] in FOREX_CURRENCIES:
        return clean[:3], clean[3:]
    if crypto_parts is not None:
        return crypto_parts
    if len(clean) == 6:
        return clean[:3], clean[3:]
    raise ValueError(f"Unsupported symbol: {symbol}")


def infer_market_type(symbol: object, *, default: MarketType = MarketType.CRYPTO_SPOT) -> MarketType:
    clean = normalize_symbol(symbol)
    if len(clean) == 6 and clean[:3] in FOREX_CURRENCIES and clean[3:] in FOREX_CURRENCIES:
        return MarketType.FOREX
    if _split_crypto_symbol(clean) is not None:
        return MarketType.CRYPTO_SPOT
    return default


def default_price_unit(symbol: object, *, market_type: object | None = None, tick_size: float | None = None) -> float:
    if tick_size is not None and tick_size > 0:
        return float(tick_size)
    resolved_market = normalize_market_type(market_type, default=infer_market_type(symbol))
    if resolved_market == MarketType.FOREX:
        _, quote = split_symbol(symbol, market_type=MarketType.FOREX)
        return 0.01 if quote == "JPY" else 0.0001
    try:
        _, quote = split_symbol(symbol, market_type=resolved_market)
    except ValueError:
        return 0.01
    if quote in STABLE_USD_QUOTES:
        return 0.01
    if quote in {"BTC", "ETH"}:
        return 0.00000001
    return 0.0001


def yahoo_ticker_for_symbol(symbol: object, *, market_type: object | None = None) -> str:
    resolved_market = normalize_market_type(market_type, default=infer_market_type(symbol))
    base, quote = split_symbol(symbol, market_type=resolved_market)
    if resolved_market == MarketType.FOREX:
        return f"{base}{quote}=X"
    yahoo_quote = "USD" if quote in STABLE_USD_QUOTES else quote
    return f"{base}-{yahoo_quote}"


@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    base: str
    quote: str
    market_type: MarketType
    exchange_symbol: str | None = None
    tick_size: float | None = None
    min_order_size: float | None = None
    quantity_step: float | None = None
    maker_fee_rate: float = 0.0
    taker_fee_rate: float = 0.0
    contract_size: float = 1.0
    price_precision: int | None = None
    quantity_precision: int | None = None

    @classmethod
    def from_symbol(
        cls,
        symbol: object,
        *,
        market_type: object | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> "SymbolSpec":
        data = dict(payload or {})
        resolved_market = normalize_market_type(data.get("market_type", market_type), default=infer_market_type(symbol))
        base, quote = split_symbol(data.get("symbol", symbol), market_type=resolved_market)
        normalized = normalize_symbol(data.get("symbol", symbol))
        exchange_symbol = str(data.get("exchange_symbol", "") or "").strip() or None
        tick_size = _optional_float(data.get("tick_size"))
        return cls(
            symbol=normalized,
            base=base,
            quote=quote,
            market_type=resolved_market,
            exchange_symbol=exchange_symbol,
            tick_size=tick_size,
            min_order_size=_optional_float(data.get("min_order_size")),
            quantity_step=_optional_float(data.get("quantity_step")),
            maker_fee_rate=max(0.0, float(data.get("maker_fee_rate", 0.0) or 0.0)),
            taker_fee_rate=max(0.0, float(data.get("taker_fee_rate", 0.0) or 0.0)),
            contract_size=max(0.0, float(data.get("contract_size", 1.0) or 1.0)),
            price_precision=_optional_int(data.get("price_precision")),
            quantity_precision=_optional_int(data.get("quantity_precision")),
        )

    @property
    def normalized(self) -> str:
        return normalize_symbol(self.symbol)

    @property
    def price_unit(self) -> float:
        return default_price_unit(self.symbol, market_type=self.market_type, tick_size=self.tick_size)

    @property
    def exchange_or_symbol(self) -> str:
        return self.exchange_symbol or self.symbol

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "base": self.base,
            "quote": self.quote,
            "market_type": self.market_type.value,
            "exchange_symbol": self.exchange_symbol,
            "tick_size": self.tick_size,
            "min_order_size": self.min_order_size,
            "quantity_step": self.quantity_step,
            "maker_fee_rate": self.maker_fee_rate,
            "taker_fee_rate": self.taker_fee_rate,
            "contract_size": self.contract_size,
            "price_precision": self.price_precision,
            "quantity_precision": self.quantity_precision,
        }


def build_symbol_specs(
    symbols: Iterable[object],
    *,
    raw_specs: Mapping[str, Any] | None = None,
    market_type: object | None = None,
) -> dict[str, SymbolSpec]:
    specs: dict[str, SymbolSpec] = {}
    for symbol in symbols:
        spec = SymbolSpec.from_symbol(symbol, market_type=market_type)
        specs[spec.normalized] = spec
    for raw_symbol, raw_payload in (raw_specs or {}).items():
        payload = raw_payload if isinstance(raw_payload, Mapping) else {}
        spec = SymbolSpec.from_symbol(raw_symbol, market_type=market_type, payload=payload)
        specs[spec.normalized] = spec
    return specs


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
