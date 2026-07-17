from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Protocol, Sequence


PHASE11 = "phase11_realtime_market_safety"
STATE_MARKET_SAFE = "market_safe"
STATE_BLOCKED = "blocked"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _money(value: object) -> float:
    return round(_as_float(value), 2)


def _clean_symbol(value: object) -> str:
    return str(value or "").strip().upper().replace("/", "")


def _exchange_symbol(value: object) -> str:
    text = str(value or "").strip().upper()
    if "/" in text:
        return text
    if text.endswith("USDT") and len(text) > 4:
        return f"{text[:-4]}/USDT"
    return text


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text).astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def _iso_from_ms(value: object) -> str | None:
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _bps(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return round(abs(numerator) / denominator * 10000.0, 6)


def _import_ccxt() -> object:
    try:
        import ccxt
    except ImportError as exc:
        raise ImportError("ccxt package not installed. Install with: pip install -r requirements.txt") from exc
    return ccxt


class RealtimeMarketDataProvider(Protocol):
    def fetch_ticker(self, symbol: str) -> Mapping[str, object]:
        ...

    def fetch_order_book(self, symbol: str, limit: int) -> Mapping[str, object]:
        ...

    def fetch_exchange_time_ms(self) -> int | None:
        ...

    def close(self) -> None:
        ...


class CcxtRealtimeMarketDataProvider:
    """Public CCXT realtime checks. This class does not load credentials or place orders."""

    def __init__(
        self,
        *,
        exchange_id: str = "binance",
        default_type: str = "spot",
        sandbox: bool = False,
        timeout_ms: int = 10000,
        enable_rate_limit: bool = True,
    ) -> None:
        self.exchange_id = str(exchange_id or "binance").strip().lower()
        self.default_type = str(default_type or "spot").strip().lower()
        self.sandbox = bool(sandbox)
        self.timeout_ms = max(1000, int(timeout_ms))
        self.enable_rate_limit = bool(enable_rate_limit)
        self._exchange: object | None = None

    def _connect(self) -> object:
        if self._exchange is not None:
            return self._exchange
        ccxt = _import_ccxt()
        exchange_class = getattr(ccxt, self.exchange_id, None)
        if exchange_class is None:
            raise ValueError(f"Unsupported CCXT exchange: {self.exchange_id}")
        exchange = exchange_class(
            {
                "enableRateLimit": self.enable_rate_limit,
                "timeout": self.timeout_ms,
                "options": {"defaultType": self.default_type},
            }
        )
        if self.sandbox and hasattr(exchange, "set_sandbox_mode"):
            exchange.set_sandbox_mode(True)
        self._exchange = exchange
        return exchange

    def fetch_ticker(self, symbol: str) -> Mapping[str, object]:
        exchange = self._connect()
        return exchange.fetch_ticker(symbol)  # type: ignore[attr-defined]

    def fetch_order_book(self, symbol: str, limit: int) -> Mapping[str, object]:
        exchange = self._connect()
        return exchange.fetch_order_book(symbol, limit=max(1, int(limit)))  # type: ignore[attr-defined]

    def fetch_exchange_time_ms(self) -> int | None:
        exchange = self._connect()
        if getattr(exchange, "has", {}).get("fetchTime"):
            return int(exchange.fetch_time())  # type: ignore[attr-defined]
        if hasattr(exchange, "milliseconds"):
            return int(exchange.milliseconds())  # type: ignore[attr-defined]
        return int(time.time() * 1000)

    def close(self) -> None:
        exchange = self._exchange
        if exchange is not None and hasattr(exchange, "close"):
            exchange.close()  # type: ignore[attr-defined]


@dataclass(frozen=True)
class CryptoMarketSafetySettings:
    phase10_report_path: Path | str = Path("reports/crypto_phase10_testnet_order_preview.json")
    report_path: Path | str = Path("reports/crypto_phase11_market_safety_report.json")
    checks_csv_path: Path | str = Path("reports/crypto_phase11_market_safety_checks.csv")
    market_data_diagnostics_path: Path | str = Path("logs/crypto_forward_market_data.jsonl")
    exchange_id: str = "binance"
    default_type: str = "spot"
    sandbox: bool = False
    timeout_ms: int = 10000
    enable_rate_limit: bool = True
    enable_order_book_check: bool = True
    order_book_limit: int = 5
    max_ticker_age_seconds: float = 30.0
    max_spread_bps: float = 10.0
    max_exchange_time_drift_ms: float = 2000.0
    max_entry_price_deviation_bps: float = 100.0
    require_phase10_ready: bool = True
    require_clean_market_diagnostics: bool = True
    diagnostics_timeframe: str = "M5"
    max_market_diagnostics_age_seconds: float = 900.0
    allow_live_orders: bool = False

    def normalized(self) -> "CryptoMarketSafetySettings":
        return CryptoMarketSafetySettings(
            phase10_report_path=Path(self.phase10_report_path),
            report_path=Path(self.report_path),
            checks_csv_path=Path(self.checks_csv_path),
            market_data_diagnostics_path=Path(self.market_data_diagnostics_path),
            exchange_id=str(self.exchange_id or "binance").strip().lower(),
            default_type=str(self.default_type or "spot").strip().lower(),
            sandbox=bool(self.sandbox),
            timeout_ms=max(1000, int(self.timeout_ms)),
            enable_rate_limit=bool(self.enable_rate_limit),
            enable_order_book_check=bool(self.enable_order_book_check),
            order_book_limit=max(1, int(self.order_book_limit)),
            max_ticker_age_seconds=max(1.0, float(self.max_ticker_age_seconds)),
            max_spread_bps=max(0.0, float(self.max_spread_bps)),
            max_exchange_time_drift_ms=max(0.0, float(self.max_exchange_time_drift_ms)),
            max_entry_price_deviation_bps=max(0.0, float(self.max_entry_price_deviation_bps)),
            require_phase10_ready=bool(self.require_phase10_ready),
            require_clean_market_diagnostics=bool(self.require_clean_market_diagnostics),
            diagnostics_timeframe=str(self.diagnostics_timeframe or "M5").strip().upper(),
            max_market_diagnostics_age_seconds=max(1.0, float(self.max_market_diagnostics_age_seconds)),
            allow_live_orders=False,
        )


def load_phase10_preview_report(path: Path | str) -> dict[str, object]:
    report_path = Path(path)
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "type": "missing_crypto_testnet_order_preview_report",
            "path": str(report_path),
            "requests": [],
            "order_submission_attempted": False,
            "live_execution_allowed": False,
            "decision": {
                "action": "MISSING_PHASE10_REPORT",
                "readiness": "missing",
                "reason": f"Phase 10 testnet order preview report missing: {report_path}",
                "live_execution_allowed": False,
                "testnet_request_preview_ready": False,
            },
        }
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "type": "invalid_crypto_testnet_order_preview_report",
            "path": str(report_path),
            "requests": [],
            "order_submission_attempted": False,
            "live_execution_allowed": False,
            "decision": {
                "action": "INVALID_PHASE10_REPORT",
                "readiness": "invalid",
                "reason": f"Phase 10 testnet order preview report unreadable: {exc}",
                "live_execution_allowed": False,
                "testnet_request_preview_ready": False,
            },
        }
    return payload if isinstance(payload, dict) else {}


def phase10_source_blocks(report: Mapping[str, object], settings: CryptoMarketSafetySettings) -> list[str]:
    decision = _as_dict(report.get("decision"))
    summary = _as_dict(report.get("summary"))
    reasons: list[str] = []
    if report.get("live_execution_allowed") is True or decision.get("live_execution_allowed") is True:
        reasons.append("phase10_live_execution_allowed_true")
    if report.get("order_submission_attempted") is True:
        reasons.append("phase10_order_submission_attempted")
    if report.get("testnet_order_submission_enabled") is True:
        reasons.append("phase10_testnet_submission_enabled")
    if _as_int(summary.get("live_order_sent")) > 0:
        reasons.append("phase10_report_has_live_order_sent")
    if settings.require_phase10_ready and decision.get("testnet_request_preview_ready") is not True:
        reasons.append("phase10_request_preview_not_ready")
    return reasons


def source_request_previews(report: Mapping[str, object]) -> list[dict[str, object]]:
    raw_requests = report.get("requests") if isinstance(report.get("requests"), list) else []
    return [
        dict(item)
        for item in raw_requests
        if isinstance(item, Mapping)
        and item.get("state") == "request_previewed"
        and item.get("order_submission_attempted") is not True
        and item.get("live_order_sent") is not True
        and isinstance(item.get("request"), Mapping)
    ]


def _diagnostic_rows(path: Path | str) -> list[dict[str, object]]:
    log_path = Path(path)
    if not log_path.exists():
        return []
    rows: list[dict[str, object]] = []
    try:
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict) and payload.get("type") == "market_data_fetch":
                rows.append(payload)
    except (OSError, json.JSONDecodeError):
        return []
    return rows


def latest_market_diagnostic(
    rows: Sequence[Mapping[str, object]],
    *,
    symbol: str,
    timeframe: str,
) -> dict[str, object] | None:
    clean_symbol = _clean_symbol(symbol)
    tf = timeframe.upper()
    matches = [
        dict(row)
        for row in rows
        if _clean_symbol(row.get("pair")) == clean_symbol and str(row.get("timeframe", "")).upper() == tf
    ]
    matches.sort(key=lambda row: str(row.get("observed_at", "")), reverse=True)
    return matches[0] if matches else None


def market_diagnostics_blocks(
    diagnostic: Mapping[str, object] | None,
    settings: CryptoMarketSafetySettings,
    observed_at: datetime,
) -> tuple[list[str], dict[str, object]]:
    if diagnostic is None:
        return ["missing_recent_market_diagnostics"], {}
    observed = _parse_datetime(diagnostic.get("observed_at"))
    age_seconds = None
    reasons: list[str] = []
    if observed is None:
        reasons.append("market_diagnostics_timestamp_missing")
    else:
        age_seconds = max(0.0, (observed_at - observed).total_seconds())
        if age_seconds > settings.max_market_diagnostics_age_seconds:
            reasons.append("market_diagnostics_too_old")
    served_from = str(diagnostic.get("served_from") or "")
    if served_from in {"stale_cache_after_provider_failure", "cached_empty_fallback"}:
        reasons.append("stale_cache_used_for_signal_data")
    if diagnostic.get("ok") is False:
        reasons.append("market_diagnostics_fetch_failed")
    if diagnostic.get("stale") is True:
        reasons.append("market_diagnostics_trigger_stale")
    details = {
        "observed_at": diagnostic.get("observed_at"),
        "age_seconds": None if age_seconds is None else round(age_seconds, 3),
        "timeframe": diagnostic.get("timeframe"),
        "served_from": served_from,
        "stale": diagnostic.get("stale"),
        "ok": diagnostic.get("ok"),
        "candle_age_seconds": diagnostic.get("candle_age_seconds"),
        "last_candle_time": diagnostic.get("last_candle_time"),
    }
    return reasons, details


def _best_order_book_side(rows: object, index: int) -> float | None:
    if not isinstance(rows, list) or not rows:
        return None
    first = rows[0]
    if not isinstance(first, (list, tuple)) or len(first) <= index:
        return None
    value = _as_float(first[index])
    return value if value > 0 else None


class CryptoMarketSafetyEngine:
    def __init__(
        self,
        settings: CryptoMarketSafetySettings,
        *,
        provider: RealtimeMarketDataProvider | None = None,
    ) -> None:
        self.settings = settings.normalized()
        self.provider = provider or CcxtRealtimeMarketDataProvider(
            exchange_id=self.settings.exchange_id,
            default_type=self.settings.default_type,
            sandbox=self.settings.sandbox,
            timeout_ms=self.settings.timeout_ms,
            enable_rate_limit=self.settings.enable_rate_limit,
        )

    def build_report(self, phase10_report: Mapping[str, object] | None = None) -> dict[str, object]:
        source = load_phase10_preview_report(self.settings.phase10_report_path) if phase10_report is None else dict(phase10_report)
        generated_at = utc_now()
        observed_at = datetime.fromisoformat(generated_at)
        source_reasons = phase10_source_blocks(source, self.settings)
        diagnostics = _diagnostic_rows(self.settings.market_data_diagnostics_path)
        checks: list[dict[str, object]] = []
        if not source_reasons:
            for request in source_request_previews(source):
                checks.append(self._check_request(request, diagnostics, observed_at))
        summary = self._summary(checks)
        decision = self._decision(summary, source_reasons)
        return {
            "type": "crypto_market_safety_report",
            "version": 1,
            "phase": PHASE11,
            "generated_at": generated_at,
            "decision": decision,
            "settings": {
                "phase10_report_path": str(self.settings.phase10_report_path),
                "exchange_id": self.settings.exchange_id,
                "default_type": self.settings.default_type,
                "sandbox": self.settings.sandbox,
                "enable_order_book_check": self.settings.enable_order_book_check,
                "order_book_limit": self.settings.order_book_limit,
                "max_ticker_age_seconds": self.settings.max_ticker_age_seconds,
                "max_spread_bps": self.settings.max_spread_bps,
                "max_exchange_time_drift_ms": self.settings.max_exchange_time_drift_ms,
                "max_entry_price_deviation_bps": self.settings.max_entry_price_deviation_bps,
                "require_phase10_ready": self.settings.require_phase10_ready,
                "require_clean_market_diagnostics": self.settings.require_clean_market_diagnostics,
                "diagnostics_timeframe": self.settings.diagnostics_timeframe,
                "max_market_diagnostics_age_seconds": self.settings.max_market_diagnostics_age_seconds,
                "allow_live_orders": False,
            },
            "source_report": {
                "type": source.get("type"),
                "phase": source.get("phase"),
                "generated_at": source.get("generated_at"),
                "decision": _as_dict(source.get("decision")),
                "summary": _as_dict(source.get("summary")),
                "order_submission_attempted": source.get("order_submission_attempted") is True,
                "live_execution_allowed": source.get("live_execution_allowed") is True,
                "block_reasons": source_reasons,
            },
            "summary": summary,
            "checks": checks,
            "execution_market_data_safe": decision.get("market_data_safe") is True,
            "order_submission_allowed": False,
            "live_execution_allowed": False,
            "safety_note": "Phase 11 performs public realtime market checks only. It does not submit testnet or live orders.",
        }

    def _check_request(
        self,
        request_preview: Mapping[str, object],
        diagnostics: Sequence[Mapping[str, object]],
        observed_at: datetime,
    ) -> dict[str, object]:
        request = _as_dict(request_preview.get("request"))
        symbol = _exchange_symbol(request.get("symbol") or request_preview.get("exchange_symbol") or request_preview.get("symbol"))
        side = str(request.get("side") or request_preview.get("side") or "").lower()
        entry_price = _as_float(request_preview.get("entry_price") or request.get("price") or 0.0)
        block_reasons: list[str] = []
        warnings: list[str] = []

        ticker: Mapping[str, object] = {}
        order_book: Mapping[str, object] = {}
        server_time_ms: int | None = None
        try:
            ticker = self.provider.fetch_ticker(symbol)
        except Exception as exc:
            block_reasons.append("ticker_fetch_failed")
            warnings.append(str(exc))
        try:
            server_time_ms = self.provider.fetch_exchange_time_ms()
        except Exception as exc:
            block_reasons.append("exchange_time_fetch_failed")
            warnings.append(str(exc))
        if self.settings.enable_order_book_check:
            try:
                order_book = self.provider.fetch_order_book(symbol, self.settings.order_book_limit)
            except Exception as exc:
                block_reasons.append("order_book_fetch_failed")
                warnings.append(str(exc))

        ticker_details, ticker_reasons = self._ticker_details(ticker, observed_at)
        block_reasons.extend(ticker_reasons)
        time_details, time_reasons = self._exchange_time_details(server_time_ms, observed_at)
        block_reasons.extend(time_reasons)
        order_book_details, ob_reasons = self._order_book_details(order_book)
        block_reasons.extend(ob_reasons)
        if self.settings.enable_order_book_check and order_book_details.get("spread_bps") is not None:
            ticker_details["spread_bps"] = order_book_details["spread_bps"]
            ticker_details["bid"] = order_book_details.get("bid") or ticker_details.get("bid")
            ticker_details["ask"] = order_book_details.get("ask") or ticker_details.get("ask")

        spread_bps = _as_float(ticker_details.get("spread_bps"))
        if spread_bps > self.settings.max_spread_bps:
            block_reasons.append("spread_above_max")

        reference_price = self._reference_price(side, ticker_details)
        entry_deviation_bps = None
        if reference_price <= 0 or entry_price <= 0:
            block_reasons.append("missing_entry_or_reference_price")
        else:
            entry_deviation_bps = _bps(entry_price - reference_price, reference_price)
            if entry_deviation_bps > self.settings.max_entry_price_deviation_bps:
                block_reasons.append("entry_price_deviation_above_max")

        diagnostics_details: dict[str, object] = {}
        if self.settings.require_clean_market_diagnostics:
            diagnostic = latest_market_diagnostic(
                diagnostics,
                symbol=_clean_symbol(request_preview.get("symbol") or symbol),
                timeframe=self.settings.diagnostics_timeframe,
            )
            diagnostics_reasons, diagnostics_details = market_diagnostics_blocks(diagnostic, self.settings, observed_at)
            block_reasons.extend(diagnostics_reasons)

        unique_reasons = sorted(set(block_reasons))
        return {
            "source_request_id": request.get("request_id"),
            "source_client_order_id": request_preview.get("source_client_order_id"),
            "source_intent_id": request_preview.get("source_intent_id"),
            "symbol": request_preview.get("symbol"),
            "exchange_symbol": symbol,
            "side": side,
            "order_type": request.get("type") or request_preview.get("order_type"),
            "entry_price": entry_price,
            "reference_price": reference_price,
            "entry_price_deviation_bps": entry_deviation_bps,
            "spread_bps": ticker_details.get("spread_bps"),
            "state": STATE_MARKET_SAFE if not unique_reasons else STATE_BLOCKED,
            "block_reasons": unique_reasons,
            "warnings": warnings,
            "ticker": ticker_details,
            "order_book": order_book_details,
            "exchange_time": time_details,
            "market_diagnostics": diagnostics_details,
            "order_submission_allowed": False,
            "live_execution_allowed": False,
        }

    def _ticker_details(self, ticker: Mapping[str, object], observed_at: datetime) -> tuple[dict[str, object], list[str]]:
        if not ticker:
            return {}, ["ticker_missing"]
        reasons: list[str] = []
        bid = _as_float(ticker.get("bid"))
        ask = _as_float(ticker.get("ask"))
        last = _as_float(ticker.get("last"))
        ticker_time = _parse_datetime(ticker.get("timestamp") or ticker.get("datetime"))
        age_seconds = 0.0
        if ticker_time is None:
            reasons.append("ticker_timestamp_missing")
        else:
            age_seconds = max(0.0, (observed_at - ticker_time).total_seconds())
            if age_seconds > self.settings.max_ticker_age_seconds:
                reasons.append("ticker_too_old")
        spread_bps = None
        if bid > 0 and ask > 0 and ask >= bid:
            spread_bps = _bps(ask - bid, (ask + bid) / 2.0)
        else:
            reasons.append("ticker_bid_ask_missing")
        return (
            {
                "bid": bid,
                "ask": ask,
                "last": last,
                "timestamp": None if ticker_time is None else ticker_time.isoformat(),
                "age_seconds": round(age_seconds, 3),
                "spread_bps": spread_bps,
            },
            reasons,
        )

    def _exchange_time_details(self, server_time_ms: int | None, observed_at: datetime) -> tuple[dict[str, object], list[str]]:
        if server_time_ms is None:
            return {}, ["exchange_time_missing"]
        local_ms = int(observed_at.timestamp() * 1000)
        drift_ms = abs(int(server_time_ms) - local_ms)
        reasons = ["exchange_time_drift_above_max"] if drift_ms > self.settings.max_exchange_time_drift_ms else []
        return (
            {
                "server_time_ms": int(server_time_ms),
                "server_time": _iso_from_ms(server_time_ms),
                "local_time": observed_at.isoformat(),
                "drift_ms": drift_ms,
            },
            reasons,
        )

    def _order_book_details(self, order_book: Mapping[str, object]) -> tuple[dict[str, object], list[str]]:
        if not self.settings.enable_order_book_check:
            return {}, []
        if not order_book:
            return {}, ["order_book_missing"]
        bid = _best_order_book_side(order_book.get("bids"), 0)
        ask = _best_order_book_side(order_book.get("asks"), 0)
        bid_size = _best_order_book_side(order_book.get("bids"), 1)
        ask_size = _best_order_book_side(order_book.get("asks"), 1)
        reasons: list[str] = []
        spread_bps = None
        if bid is None or ask is None or ask < bid:
            reasons.append("order_book_top_of_book_invalid")
        else:
            spread_bps = _bps(ask - bid, (ask + bid) / 2.0)
        return (
            {
                "bid": bid,
                "ask": ask,
                "bid_size": bid_size,
                "ask_size": ask_size,
                "spread_bps": spread_bps,
            },
            reasons,
        )

    @staticmethod
    def _reference_price(side: str, ticker_details: Mapping[str, object]) -> float:
        if side == "buy":
            return _as_float(ticker_details.get("ask")) or _as_float(ticker_details.get("last"))
        if side == "sell":
            return _as_float(ticker_details.get("bid")) or _as_float(ticker_details.get("last"))
        return _as_float(ticker_details.get("last")) or _as_float(ticker_details.get("ask")) or _as_float(ticker_details.get("bid"))

    def _summary(self, checks: Sequence[Mapping[str, object]]) -> dict[str, object]:
        blocked = [check for check in checks if check.get("state") == STATE_BLOCKED]
        ready = [check for check in checks if check.get("state") == STATE_MARKET_SAFE]
        reasons: dict[str, int] = {}
        for check in blocked:
            for reason in check.get("block_reasons", []):
                reasons[str(reason)] = reasons.get(str(reason), 0) + 1
        return {
            "requests_checked": len(checks),
            "market_safe": len(ready),
            "blocked": len(blocked),
            "block_reasons": reasons,
            "order_submission_allowed": 0,
            "live_execution_allowed": 0,
        }

    @staticmethod
    def _decision(summary: Mapping[str, object], source_reasons: Sequence[str]) -> dict[str, object]:
        if source_reasons:
            return {
                "action": "BLOCK_PHASE10_SOURCE",
                "readiness": "blocked",
                "reason": ",".join(source_reasons),
                "market_data_safe": False,
                "order_submission_allowed": False,
                "live_execution_allowed": False,
            }
        if _as_int(summary.get("requests_checked")) <= 0:
            return {
                "action": "COLLECT_PHASE10_PREVIEWS",
                "readiness": "collecting",
                "reason": "no Phase 10 request previews available for realtime market safety checks",
                "market_data_safe": False,
                "order_submission_allowed": False,
                "live_execution_allowed": False,
            }
        if _as_int(summary.get("blocked")) > 0:
            return {
                "action": "BLOCK_EXECUTION_MARKET_UNSAFE",
                "readiness": "blocked",
                "reason": "one or more realtime market safety checks failed",
                "market_data_safe": False,
                "order_submission_allowed": False,
                "live_execution_allowed": False,
            }
        return {
            "action": "REALTIME_MARKET_SAFETY_READY",
            "readiness": "market_safe",
            "reason": "realtime ticker, spread, exchange time, and diagnostics checks passed",
            "market_data_safe": True,
            "order_submission_allowed": False,
            "live_execution_allowed": False,
        }

    def close(self) -> None:
        self.provider.close()


def write_crypto_market_safety_outputs(report: Mapping[str, object], settings: CryptoMarketSafetySettings) -> None:
    cfg = settings.normalized()
    report_path = Path(cfg.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")

    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    csv_path = Path(cfg.checks_csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "source_request_id",
        "source_client_order_id",
        "source_intent_id",
        "symbol",
        "exchange_symbol",
        "side",
        "order_type",
        "state",
        "block_reasons",
        "entry_price",
        "reference_price",
        "entry_price_deviation_bps",
        "spread_bps",
        "order_submission_allowed",
        "live_execution_allowed",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for check in checks:
            if not isinstance(check, Mapping):
                continue
            row = dict(check)
            row["block_reasons"] = ",".join(str(reason) for reason in check.get("block_reasons", []))
            writer.writerow(row)
