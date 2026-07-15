"""CCXT market data provider for exchange-native crypto candles."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd

from core.symbols import MarketType, SymbolSpec, build_symbol_specs, normalize_market_type, normalize_symbol, split_symbol
from data.market_data_base import MarketDataProvider, TIMEFRAME_MAP, register_provider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CcxtConfig:
    exchange_id: str = "binance"
    market_type: MarketType = MarketType.CRYPTO_SPOT
    default_type: str = "spot"
    sandbox: bool = False
    timeout_ms: int = 10000
    enable_rate_limit: bool = True
    ohlcv_limit: int | None = None
    health_check_symbol: str = "BTCUSDT"
    timeframe_map: dict[str, str] | None = None
    ohlcv_params: dict[str, object] | None = None
    exchange_options: dict[str, object] | None = None
    symbol_specs: dict[str, SymbolSpec] | None = None

    @classmethod
    def from_env(cls) -> "CcxtConfig":
        return cls.from_dict(
            {
                "exchange_id": os.getenv("CCXT_EXCHANGE_ID", "binance"),
                "market_type": os.getenv("MARKET_TYPE", "crypto_spot"),
                "default_type": os.getenv("CCXT_DEFAULT_TYPE", ""),
                "sandbox": os.getenv("CCXT_SANDBOX", "0"),
                "timeout_ms": os.getenv("CCXT_TIMEOUT_MS", "10000"),
                "enable_rate_limit": os.getenv("CCXT_ENABLE_RATE_LIMIT", "1"),
                "ohlcv_limit": os.getenv("CCXT_OHLCV_LIMIT", ""),
                "health_check_symbol": os.getenv("CCXT_HEALTH_CHECK_SYMBOL", "BTCUSDT"),
                "timeframe_map": _parse_json_object(os.getenv("CCXT_TIMEFRAME_MAP_JSON", "")),
                "ohlcv_params": _parse_json_object(os.getenv("CCXT_OHLCV_PARAMS_JSON", "")),
                "exchange_options": _parse_json_object(os.getenv("CCXT_EXCHANGE_OPTIONS_JSON", "")),
                "symbol_specs": _parse_json_object(os.getenv("SYMBOL_SPECS_JSON", "")),
            }
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "CcxtConfig":
        if not payload:
            payload = {}
        env_payload = {
            "exchange_id": os.getenv("CCXT_EXCHANGE_ID", "binance"),
            "market_type": os.getenv("MARKET_TYPE", "crypto_spot"),
            "default_type": os.getenv("CCXT_DEFAULT_TYPE", ""),
            "sandbox": os.getenv("CCXT_SANDBOX", "0"),
            "timeout_ms": os.getenv("CCXT_TIMEOUT_MS", "10000"),
            "enable_rate_limit": os.getenv("CCXT_ENABLE_RATE_LIMIT", "1"),
            "ohlcv_limit": os.getenv("CCXT_OHLCV_LIMIT", ""),
            "health_check_symbol": os.getenv("CCXT_HEALTH_CHECK_SYMBOL", "BTCUSDT"),
            "timeframe_map": _parse_json_object(os.getenv("CCXT_TIMEFRAME_MAP_JSON", "")),
            "ohlcv_params": _parse_json_object(os.getenv("CCXT_OHLCV_PARAMS_JSON", "")),
            "exchange_options": _parse_json_object(os.getenv("CCXT_EXCHANGE_OPTIONS_JSON", "")),
            "symbol_specs": _parse_json_object(os.getenv("SYMBOL_SPECS_JSON", "")),
        }
        merged = {**env_payload, **dict(payload)}
        market_type = normalize_market_type(merged.get("market_type"), default=MarketType.CRYPTO_SPOT)
        default_type = str(merged.get("default_type") or "").strip().lower()
        if not default_type:
            default_type = "spot" if market_type == MarketType.CRYPTO_SPOT else "swap"
        raw_symbol_specs = merged.get("symbol_specs") if isinstance(merged.get("symbol_specs"), Mapping) else {}
        return cls(
            exchange_id=str(merged.get("exchange_id") or "binance").strip().lower(),
            market_type=market_type,
            default_type=default_type,
            sandbox=_parse_bool(merged.get("sandbox"), default=False),
            timeout_ms=max(1000, int(merged.get("timeout_ms") or 10000)),
            enable_rate_limit=_parse_bool(merged.get("enable_rate_limit"), default=True),
            ohlcv_limit=_optional_int(merged.get("ohlcv_limit")),
            health_check_symbol=normalize_symbol(merged.get("health_check_symbol") or "BTCUSDT"),
            timeframe_map=_string_map(merged.get("timeframe_map")),
            ohlcv_params=dict(merged.get("ohlcv_params") or {}) if isinstance(merged.get("ohlcv_params"), Mapping) else {},
            exchange_options=dict(merged.get("exchange_options") or {})
            if isinstance(merged.get("exchange_options"), Mapping)
            else {},
            symbol_specs=build_symbol_specs((), raw_specs=raw_symbol_specs, market_type=market_type),
        )


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Invalid JSON object in CCXT config: %s", text[:120])
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_bool(value: object, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _optional_int(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = int(text)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _string_map(value: object) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    output = {str(key).upper(): str(item) for key, item in value.items() if str(key).strip()}
    return output or None


def _import_ccxt() -> Any:
    try:
        import ccxt
    except ImportError as exc:
        raise ImportError("ccxt package not installed. Install with: pip install -r requirements.txt") from exc
    return ccxt


class CcxtMarketDataProvider(MarketDataProvider):
    """Public OHLCV provider backed by CCXT REST APIs."""

    def __init__(self, config: CcxtConfig | None = None, history_limit: int = 500) -> None:
        super().__init__(history_limit)
        self.config = config or CcxtConfig.from_env()
        self._exchange: Any | None = None
        self._markets_loaded = False

    def _create_exchange(self) -> Any:
        ccxt = _import_ccxt()
        exchange_class = getattr(ccxt, self.config.exchange_id, None)
        if exchange_class is None:
            raise ValueError(f"Unsupported CCXT exchange: {self.config.exchange_id}")

        options = {"defaultType": self.config.default_type}
        options.update(self.config.exchange_options or {})
        exchange = exchange_class(
            {
                "enableRateLimit": self.config.enable_rate_limit,
                "timeout": self.config.timeout_ms,
                "options": options,
            }
        )
        if self.config.sandbox and hasattr(exchange, "set_sandbox_mode"):
            exchange.set_sandbox_mode(True)
        return exchange

    def _connect(self) -> Any:
        if self._exchange is None:
            self._exchange = self._create_exchange()
            self._initialized = True
        return self._exchange

    def _load_markets(self) -> None:
        exchange = self._connect()
        if not self._markets_loaded and hasattr(exchange, "load_markets"):
            exchange.load_markets()
            self._markets_loaded = True

    def _format_symbol(self, symbol: str) -> str:
        normalized = normalize_symbol(symbol)
        spec = (self.config.symbol_specs or {}).get(normalized)
        if spec is not None and spec.exchange_symbol:
            return spec.exchange_symbol
        base, quote = split_symbol(symbol, market_type=self.config.market_type)
        return f"{base}/{quote}"

    def _format_timeframe(self, timeframe: str) -> str:
        key = timeframe.upper()
        if key not in TIMEFRAME_MAP:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        default_map = {
            "M1": "1m",
            "M5": "5m",
            "M15": "15m",
            "M30": "30m",
            "H1": "1h",
            "H4": "4h",
            "D1": "1d",
        }
        mapping = self.config.timeframe_map or default_map
        return str(mapping.get(key, default_map[key]))

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int | None = None) -> pd.DataFrame:
        exchange = self._connect()
        if not bool(getattr(exchange, "has", {}).get("fetchOHLCV", True)):
            raise ConnectionError(f"Exchange {self.config.exchange_id} does not support fetchOHLCV")

        self._load_markets()
        max_rows = max(1, int(limit or self.config.ohlcv_limit or self.history_limit))
        exchange_symbol = self._format_symbol(symbol)
        exchange_timeframe = self._format_timeframe(timeframe)
        rows = exchange.fetch_ohlcv(
            exchange_symbol,
            timeframe=exchange_timeframe,
            since=None,
            limit=max_rows,
            params=dict(self.config.ohlcv_params or {}),
        )
        if not rows:
            raise ValueError(f"No CCXT OHLCV rows for {symbol} {timeframe} on {self.config.exchange_id}")

        frame = pd.DataFrame(rows)
        if frame.shape[1] < 6:
            raise ValueError(f"Malformed CCXT OHLCV rows for {symbol} {timeframe} on {self.config.exchange_id}")
        frame = frame.iloc[:, :6]
        frame.columns = ["timestamp", "open", "high", "low", "close", "volume"]
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
        frame = frame.set_index("timestamp")
        frame = frame[["open", "high", "low", "close", "volume"]].astype(float)
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        frame = self._validate_dataframe(frame, source=f"ccxt:{self.config.exchange_id}")
        self._log_data_integrity(frame, symbol, timeframe)
        return frame.tail(max_rows).copy()

    def health_check(self) -> bool:
        try:
            self.fetch_ohlcv(self.config.health_check_symbol, "M5", limit=1)
            return True
        except Exception as exc:
            logger.warning("CCXT health check failed for %s: %s", self.config.exchange_id, exc)
            return False

    def close(self) -> None:
        exchange = self._exchange
        close = getattr(exchange, "close", None)
        if callable(close):
            close()
        self._exchange = None
        self._markets_loaded = False
        super().close()


register_provider("ccxt", CcxtMarketDataProvider)
