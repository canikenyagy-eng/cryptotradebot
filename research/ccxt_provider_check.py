from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.symbols import normalize_symbol
from data.market_data import MarketDataCacheConfig, MarketDataClient, MarketDataDiagnosticsConfig


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke-check CCXT exchange-native OHLCV data.")
    parser.add_argument("--pairs", default=None, help="Comma-separated symbols. Defaults to PAIRS/settings.")
    parser.add_argument("--timeframes", default="M5,M15,H1")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--exchange", default=None, help="Override CCXT exchange id, e.g. binance, bybit, okx")
    return parser


def parse_csv(raw: str) -> list[str]:
    return [normalize_symbol(item) for item in raw.split(",") if item.strip()]


def load_env_file() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"").strip("'"))


def parse_json_dict(raw: str | None) -> dict[str, object]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_bool(raw: str | None, *, default: bool = False) -> bool:
    text = str(raw or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def parse_optional_int(raw: str | None) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = int(text)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def main() -> None:
    configure_logging()
    load_env_file()
    args = build_parser().parse_args()
    market_type = os.getenv("MARKET_TYPE", "crypto_spot").strip().lower()
    symbol_specs = parse_json_dict(os.getenv("SYMBOL_SPECS_JSON"))
    pairs = parse_csv(args.pairs or os.getenv("PAIRS", "BTCUSDT,ETHUSDT,LTCUSDT"))
    ccxt_config = {
        "exchange_id": args.exchange or os.getenv("CCXT_EXCHANGE_ID", "binance"),
        "market_type": market_type,
        "default_type": os.getenv("CCXT_DEFAULT_TYPE", "spot"),
        "sandbox": parse_bool(os.getenv("CCXT_SANDBOX"), default=False),
        "timeout_ms": int(os.getenv("CCXT_TIMEOUT_MS", "10000")),
        "enable_rate_limit": parse_bool(os.getenv("CCXT_ENABLE_RATE_LIMIT"), default=True),
        "ohlcv_limit": parse_optional_int(os.getenv("CCXT_OHLCV_LIMIT")),
        "ohlcv_request_limit": int(os.getenv("CCXT_OHLCV_REQUEST_LIMIT", "1000")),
        "health_check_symbol": os.getenv("CCXT_HEALTH_CHECK_SYMBOL", "BTCUSDT"),
        "timeframe_map": parse_json_dict(os.getenv("CCXT_TIMEFRAME_MAP_JSON")),
        "ohlcv_params": parse_json_dict(os.getenv("CCXT_OHLCV_PARAMS_JSON")),
        "exchange_options": parse_json_dict(os.getenv("CCXT_EXCHANGE_OPTIONS_JSON")),
        "symbol_specs": symbol_specs,
    }
    client = MarketDataClient(
        history_limit=max(args.limit, int(os.getenv("HISTORY_LIMIT", "500"))),
        data_source="ccxt",
        market_type=market_type,
        symbol_specs=symbol_specs,
        ccxt_config=ccxt_config,
        cache_config=MarketDataCacheConfig(enabled=False, mode="disabled"),
        diagnostics_config=MarketDataDiagnosticsConfig(enabled=False),
    )
    try:
        print(f"health={client.health_check()}")
        failures = 0
        for pair in pairs:
            for timeframe in parse_csv(args.timeframes):
                try:
                    frame = client.fetch_ohlcv(pair, timeframe, limit=args.limit)
                    last_time = frame.index[-1].isoformat() if not frame.empty else "-"
                    last_close = float(frame["close"].iloc[-1]) if not frame.empty else 0.0
                    print(
                        f"{pair:<10} {timeframe:<4} rows={len(frame):<4} "
                        f"last={last_time:<30} close={last_close}"
                    )
                except Exception as exc:
                    failures += 1
                    print(f"{pair:<10} {timeframe:<4} ERROR {exc}")
        if failures:
            raise SystemExit(1)
    finally:
        client.close()


if __name__ == "__main__":
    main()
