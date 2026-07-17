from __future__ import annotations

import asyncio
import csv
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from services.crypto_market_safety import (
    CcxtRealtimeMarketDataProvider,
    _as_float,
    _as_int,
    _bps,
    _clean_symbol,
    _diagnostic_rows,
    _exchange_symbol,
    _iso_from_ms,
    _parse_datetime,
    latest_market_diagnostic,
    market_diagnostics_blocks,
)


PHASE12 = "phase12_realtime_final_guard"
STATE_GUARD_READY = "final_guard_ready"
STATE_BLOCKED = "blocked"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _positive_float(value: object) -> float | None:
    parsed = _as_float(value)
    return parsed if parsed > 0 else None


def _stream_symbol(symbol: str) -> str:
    return _clean_symbol(symbol).lower()


def _parse_json_mapping(text: str) -> dict[str, object]:
    payload = json.loads(text)
    return payload if isinstance(payload, dict) else {}


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _dt(value: object) -> datetime | None:
    return _parse_datetime(value)


def _age_seconds(value: object, observed_at: datetime) -> float | None:
    timestamp = _dt(value)
    if timestamp is None:
        return None
    return max(0.0, (observed_at - timestamp).total_seconds())


class RealtimeFallbackProvider(Protocol):
    def fetch_ticker(self, symbol: str) -> Mapping[str, object]:
        ...

    def fetch_order_book(self, symbol: str, limit: int) -> Mapping[str, object]:
        ...

    def fetch_exchange_time_ms(self) -> int | None:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    exchange_symbol: str
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    bid_size: float | None = None
    ask_size: float | None = None
    book_ticker_observed_at: str | None = None
    last_observed_at: str | None = None
    observed_at: str | None = None
    source: str = "unknown"
    update_id: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "exchange_symbol": self.exchange_symbol,
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last,
            "bid_size": self.bid_size,
            "ask_size": self.ask_size,
            "book_ticker_observed_at": self.book_ticker_observed_at,
            "last_observed_at": self.last_observed_at,
            "observed_at": self.observed_at,
            "source": self.source,
            "update_id": self.update_id,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "MarketSnapshot":
        symbol = _clean_symbol(payload.get("symbol"))
        return cls(
            symbol=symbol,
            exchange_symbol=_exchange_symbol(payload.get("exchange_symbol") or symbol),
            bid=_positive_float(payload.get("bid")),
            ask=_positive_float(payload.get("ask")),
            last=_positive_float(payload.get("last")),
            bid_size=_positive_float(payload.get("bid_size")),
            ask_size=_positive_float(payload.get("ask_size")),
            book_ticker_observed_at=str(payload.get("book_ticker_observed_at") or "") or None,
            last_observed_at=str(payload.get("last_observed_at") or "") or None,
            observed_at=str(payload.get("observed_at") or "") or None,
            source=str(payload.get("source") or "unknown"),
            update_id=_as_int(payload.get("update_id")) or None,
        )


class RealtimeSnapshotStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else None
        self._snapshots: dict[str, MarketSnapshot] = {}
        if self.path is not None:
            self.load()

    def load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        raw_snapshots = payload.get("snapshots") if isinstance(payload, Mapping) else {}
        if not isinstance(raw_snapshots, Mapping):
            return
        loaded: dict[str, MarketSnapshot] = {}
        for key, value in raw_snapshots.items():
            if isinstance(value, Mapping):
                snapshot = MarketSnapshot.from_dict(value)
                loaded[_clean_symbol(key or snapshot.symbol)] = snapshot
        self._snapshots = loaded

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "type": "crypto_realtime_snapshot_store",
            "version": 1,
            "updated_at": utc_now(),
            "snapshots": {symbol: snapshot.to_dict() for symbol, snapshot in sorted(self._snapshots.items())},
        }
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
        tmp_path.replace(self.path)

    def get(self, symbol: str) -> MarketSnapshot | None:
        return self._snapshots.get(_clean_symbol(symbol))

    def all(self) -> list[MarketSnapshot]:
        return [self._snapshots[key] for key in sorted(self._snapshots)]

    def upsert(self, snapshot: MarketSnapshot) -> MarketSnapshot:
        self._snapshots[_clean_symbol(snapshot.symbol)] = snapshot
        return snapshot

    def update_from_websocket_payload(self, payload: Mapping[str, object], observed_at: datetime | None = None) -> MarketSnapshot | None:
        data = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
        if not isinstance(data, Mapping):
            return None
        if data.get("b") is not None and data.get("a") is not None:
            return self.update_book_ticker(data, observed_at=observed_at)
        if data.get("e") == "24hrMiniTicker" or (data.get("c") is not None and data.get("s") is not None):
            return self.update_mini_ticker(data, observed_at=observed_at)
        return None

    def update_book_ticker(self, payload: Mapping[str, object], observed_at: datetime | None = None) -> MarketSnapshot | None:
        symbol = _clean_symbol(payload.get("s"))
        if not symbol:
            return None
        observed = (observed_at or _now_dt()).isoformat()
        previous = self.get(symbol)
        source = "websocket_book_ticker"
        if previous and previous.last is not None:
            source = "websocket_book_ticker+mini_ticker"
        snapshot = MarketSnapshot(
            symbol=symbol,
            exchange_symbol=_exchange_symbol(symbol),
            bid=_positive_float(payload.get("b")),
            ask=_positive_float(payload.get("a")),
            last=previous.last if previous else None,
            bid_size=_positive_float(payload.get("B")),
            ask_size=_positive_float(payload.get("A")),
            book_ticker_observed_at=observed,
            last_observed_at=previous.last_observed_at if previous else None,
            observed_at=observed,
            source=source,
            update_id=_as_int(payload.get("u")) or None,
        )
        return self.upsert(snapshot)

    def update_mini_ticker(self, payload: Mapping[str, object], observed_at: datetime | None = None) -> MarketSnapshot | None:
        symbol = _clean_symbol(payload.get("s"))
        if not symbol:
            return None
        event_time = _parse_datetime(payload.get("E"))
        observed = (observed_at or event_time or _now_dt()).isoformat()
        previous = self.get(symbol)
        source = "websocket_mini_ticker"
        if previous and previous.source.startswith("websocket_book_ticker"):
            source = "websocket_book_ticker+mini_ticker"
        snapshot = MarketSnapshot(
            symbol=symbol,
            exchange_symbol=_exchange_symbol(symbol),
            bid=previous.bid if previous else None,
            ask=previous.ask if previous else None,
            last=_positive_float(payload.get("c")),
            bid_size=previous.bid_size if previous else None,
            ask_size=previous.ask_size if previous else None,
            book_ticker_observed_at=previous.book_ticker_observed_at if previous else None,
            last_observed_at=observed,
            observed_at=observed,
            source=source,
            update_id=previous.update_id if previous else None,
        )
        return self.upsert(snapshot)

    def update_from_ticker(self, symbol: str, ticker: Mapping[str, object], observed_at: datetime | None = None) -> MarketSnapshot:
        clean_symbol = _clean_symbol(symbol or ticker.get("symbol"))
        observed = (observed_at or _now_dt()).isoformat()
        ticker_time = _parse_datetime(ticker.get("timestamp") or ticker.get("datetime"))
        timestamp = (ticker_time or observed_at or _now_dt()).isoformat()
        snapshot = MarketSnapshot(
            symbol=clean_symbol,
            exchange_symbol=_exchange_symbol(ticker.get("symbol") or clean_symbol),
            bid=_positive_float(ticker.get("bid")),
            ask=_positive_float(ticker.get("ask")),
            last=_positive_float(ticker.get("last")),
            bid_size=_positive_float(ticker.get("bidVolume")),
            ask_size=_positive_float(ticker.get("askVolume")),
            book_ticker_observed_at=timestamp,
            last_observed_at=timestamp,
            observed_at=observed,
            source="rest_ticker_fallback",
            update_id=None,
        )
        return self.upsert(snapshot)


class BinanceBookTickerWebSocketClient:
    """Collects public Binance spot best bid/ask and mini-ticker last price snapshots."""

    def __init__(
        self,
        *,
        symbols: Sequence[str],
        store: RealtimeSnapshotStore,
        base_url: str = "wss://stream.binance.com:9443",
        open_timeout_seconds: float = 10.0,
    ) -> None:
        self.symbols = tuple(_clean_symbol(symbol) for symbol in symbols if _clean_symbol(symbol))
        self.store = store
        self.base_url = str(base_url or "wss://stream.binance.com:9443").rstrip("/")
        self.open_timeout_seconds = max(1.0, float(open_timeout_seconds))

    def stream_names(self) -> list[str]:
        streams: list[str] = []
        for symbol in self.symbols:
            stream_symbol = _stream_symbol(symbol)
            streams.append(f"{stream_symbol}@bookTicker")
            streams.append(f"{stream_symbol}@miniTicker")
        return streams

    def url(self) -> str:
        return f"{self.base_url}/stream?streams={'/'.join(self.stream_names())}"

    async def collect_for(
        self,
        *,
        duration_seconds: float = 15.0,
        stop_when_all_ready: bool = True,
    ) -> dict[str, object]:
        try:
            import websockets
        except ImportError as exc:
            raise ImportError("websockets package not installed. Install with: pip install -r requirements.txt") from exc

        started_at = _now_dt()
        deadline = time.monotonic() + max(1.0, float(duration_seconds))
        messages = 0
        updates = 0
        async with websockets.connect(
            self.url(),
            ping_interval=20,
            open_timeout=self.open_timeout_seconds,
            close_timeout=5,
        ) as websocket:
            while time.monotonic() < deadline:
                timeout = max(0.1, deadline - time.monotonic())
                try:
                    raw_message = await asyncio.wait_for(websocket.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    break
                messages += 1
                try:
                    payload = _parse_json_mapping(str(raw_message))
                except json.JSONDecodeError:
                    continue
                if self.store.update_from_websocket_payload(payload, observed_at=_now_dt()) is not None:
                    updates += 1
                    if stop_when_all_ready and self._all_symbols_have_complete_snapshots():
                        break
        self.store.save()
        return {
            "type": "crypto_realtime_websocket_collection",
            "started_at": started_at.isoformat(),
            "completed_at": utc_now(),
            "url": self.url(),
            "symbols": list(self.symbols),
            "messages": messages,
            "updates": updates,
            "snapshots": [snapshot.to_dict() for snapshot in self.store.all()],
        }

    def _all_symbols_have_complete_snapshots(self) -> bool:
        for symbol in self.symbols:
            snapshot = self.store.get(symbol)
            if snapshot is None or snapshot.bid is None or snapshot.ask is None or snapshot.last is None:
                return False
        return True


@dataclass(frozen=True)
class CryptoRealtimeGuardSettings:
    phase11_report_path: Path | str = Path("reports/crypto_phase11_market_safety_report.json")
    report_path: Path | str = Path("reports/crypto_phase12_realtime_guard_report.json")
    checks_csv_path: Path | str = Path("reports/crypto_phase12_realtime_guard_checks.csv")
    snapshot_path: Path | str = Path("logs/crypto_realtime_snapshots.json")
    market_data_diagnostics_path: Path | str = Path("logs/crypto_forward_market_data.jsonl")
    symbols: tuple[str, ...] | str = ("BTCUSDT", "ETHUSDT")
    exchange_id: str = "binance"
    default_type: str = "spot"
    sandbox: bool = False
    timeout_ms: int = 10000
    enable_rate_limit: bool = True
    allow_rest_fallback: bool = True
    enable_order_book_refresh: bool = True
    order_book_limit: int = 5
    max_snapshot_age_seconds: float = 10.0
    max_spread_bps: float = 10.0
    max_entry_price_deviation_bps: float = 50.0
    max_exchange_time_drift_ms: float = 2000.0
    require_phase11_ready: bool = True
    require_clean_market_diagnostics: bool = True
    diagnostics_timeframe: str = "M5"
    max_market_diagnostics_age_seconds: float = 900.0
    allow_live_orders: bool = False

    def normalized(self) -> "CryptoRealtimeGuardSettings":
        return CryptoRealtimeGuardSettings(
            phase11_report_path=Path(self.phase11_report_path),
            report_path=Path(self.report_path),
            checks_csv_path=Path(self.checks_csv_path),
            snapshot_path=Path(self.snapshot_path),
            market_data_diagnostics_path=Path(self.market_data_diagnostics_path),
            symbols=_clean_symbols(self.symbols),
            exchange_id=str(self.exchange_id or "binance").strip().lower(),
            default_type=str(self.default_type or "spot").strip().lower(),
            sandbox=bool(self.sandbox),
            timeout_ms=max(1000, int(self.timeout_ms)),
            enable_rate_limit=bool(self.enable_rate_limit),
            allow_rest_fallback=bool(self.allow_rest_fallback),
            enable_order_book_refresh=bool(self.enable_order_book_refresh),
            order_book_limit=max(1, int(self.order_book_limit)),
            max_snapshot_age_seconds=max(1.0, float(self.max_snapshot_age_seconds)),
            max_spread_bps=max(0.0, float(self.max_spread_bps)),
            max_entry_price_deviation_bps=max(0.0, float(self.max_entry_price_deviation_bps)),
            max_exchange_time_drift_ms=max(0.0, float(self.max_exchange_time_drift_ms)),
            require_phase11_ready=bool(self.require_phase11_ready),
            require_clean_market_diagnostics=bool(self.require_clean_market_diagnostics),
            diagnostics_timeframe=str(self.diagnostics_timeframe or "M5").strip().upper(),
            max_market_diagnostics_age_seconds=max(1.0, float(self.max_market_diagnostics_age_seconds)),
            allow_live_orders=False,
        )


def _clean_symbols(value: Sequence[str] | str) -> tuple[str, ...]:
    raw = value.split(",") if isinstance(value, str) else value
    return tuple(sorted({_clean_symbol(symbol) for symbol in raw if _clean_symbol(symbol)}))


def load_phase11_market_safety_report(path: Path | str) -> dict[str, object]:
    report_path = Path(path)
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "type": "missing_crypto_market_safety_report",
            "path": str(report_path),
            "checks": [],
            "order_submission_allowed": False,
            "live_execution_allowed": False,
            "decision": {
                "action": "MISSING_PHASE11_REPORT",
                "readiness": "missing",
                "reason": f"Phase 11 market safety report missing: {report_path}",
                "market_data_safe": False,
                "order_submission_allowed": False,
                "live_execution_allowed": False,
            },
        }
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "type": "invalid_crypto_market_safety_report",
            "path": str(report_path),
            "checks": [],
            "order_submission_allowed": False,
            "live_execution_allowed": False,
            "decision": {
                "action": "INVALID_PHASE11_REPORT",
                "readiness": "invalid",
                "reason": f"Phase 11 market safety report unreadable: {exc}",
                "market_data_safe": False,
                "order_submission_allowed": False,
                "live_execution_allowed": False,
            },
        }
    return payload if isinstance(payload, dict) else {}


def phase11_source_blocks(report: Mapping[str, object], settings: CryptoRealtimeGuardSettings) -> list[str]:
    decision = _as_dict(report.get("decision"))
    summary = _as_dict(report.get("summary"))
    reasons: list[str] = []
    report_type = str(report.get("type") or "")
    if report_type == "missing_crypto_market_safety_report":
        reasons.append("phase11_report_missing")
    if report_type == "invalid_crypto_market_safety_report":
        reasons.append("phase11_report_invalid")
    if report.get("live_execution_allowed") is True or decision.get("live_execution_allowed") is True:
        reasons.append("phase11_live_execution_allowed_true")
    if report.get("order_submission_allowed") is True or decision.get("order_submission_allowed") is True:
        reasons.append("phase11_order_submission_allowed_true")
    if _as_int(summary.get("live_execution_allowed")) > 0 or _as_int(summary.get("order_submission_allowed")) > 0:
        reasons.append("phase11_summary_allows_orders")
    if settings.require_phase11_ready and decision.get("market_data_safe") is not True:
        reasons.append("phase11_market_safety_not_ready")
    return reasons


def source_phase11_checks(report: Mapping[str, object], settings: CryptoRealtimeGuardSettings) -> list[dict[str, object]]:
    raw_checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    allowed_symbols = set(settings.symbols)
    checks: list[dict[str, object]] = []
    for check in raw_checks:
        if not isinstance(check, Mapping):
            continue
        symbol = _clean_symbol(check.get("symbol") or check.get("exchange_symbol"))
        if allowed_symbols and symbol not in allowed_symbols:
            continue
        if check.get("state") != "market_safe":
            continue
        if check.get("live_execution_allowed") is True or check.get("order_submission_allowed") is True:
            continue
        checks.append(dict(check))
    return checks


class CryptoRealtimeGuardEngine:
    def __init__(
        self,
        settings: CryptoRealtimeGuardSettings,
        *,
        store: RealtimeSnapshotStore | None = None,
        provider: RealtimeFallbackProvider | None = None,
    ) -> None:
        self.settings = settings.normalized()
        self.store = store or RealtimeSnapshotStore(self.settings.snapshot_path)
        self.provider = provider or CcxtRealtimeMarketDataProvider(
            exchange_id=self.settings.exchange_id,
            default_type=self.settings.default_type,
            sandbox=self.settings.sandbox,
            timeout_ms=self.settings.timeout_ms,
            enable_rate_limit=self.settings.enable_rate_limit,
        )

    def build_report(self, phase11_report: Mapping[str, object] | None = None) -> dict[str, object]:
        source = load_phase11_market_safety_report(self.settings.phase11_report_path) if phase11_report is None else dict(phase11_report)
        generated_at = utc_now()
        observed_at = datetime.fromisoformat(generated_at)
        source_reasons = phase11_source_blocks(source, self.settings)
        diagnostics = _diagnostic_rows(self.settings.market_data_diagnostics_path)
        checks: list[dict[str, object]] = []
        if not source_reasons:
            for source_check in source_phase11_checks(source, self.settings):
                checks.append(self._check_source(source_check, diagnostics, observed_at))
        summary = self._summary(checks)
        decision = self._decision(summary, source_reasons)
        return {
            "type": "crypto_realtime_final_guard_report",
            "version": 1,
            "phase": PHASE12,
            "generated_at": generated_at,
            "decision": decision,
            "settings": {
                "phase11_report_path": str(self.settings.phase11_report_path),
                "snapshot_path": str(self.settings.snapshot_path),
                "symbols": list(self.settings.symbols),
                "exchange_id": self.settings.exchange_id,
                "default_type": self.settings.default_type,
                "sandbox": self.settings.sandbox,
                "allow_rest_fallback": self.settings.allow_rest_fallback,
                "enable_order_book_refresh": self.settings.enable_order_book_refresh,
                "order_book_limit": self.settings.order_book_limit,
                "max_snapshot_age_seconds": self.settings.max_snapshot_age_seconds,
                "max_spread_bps": self.settings.max_spread_bps,
                "max_entry_price_deviation_bps": self.settings.max_entry_price_deviation_bps,
                "max_exchange_time_drift_ms": self.settings.max_exchange_time_drift_ms,
                "require_phase11_ready": self.settings.require_phase11_ready,
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
                "execution_market_data_safe": source.get("execution_market_data_safe") is True,
                "order_submission_allowed": source.get("order_submission_allowed") is True,
                "live_execution_allowed": source.get("live_execution_allowed") is True,
                "block_reasons": source_reasons,
            },
            "summary": summary,
            "checks": checks,
            "paper_testnet_readiness_allowed": decision.get("paper_testnet_readiness_allowed") is True,
            "order_submission_allowed": False,
            "live_execution_allowed": False,
            "safety_note": "Phase 12 performs final realtime pre-order guard checks only. It can mark paper/testnet readiness, but it does not submit testnet or live orders.",
        }

    def _check_source(
        self,
        source_check: Mapping[str, object],
        diagnostics: Sequence[Mapping[str, object]],
        observed_at: datetime,
    ) -> dict[str, object]:
        symbol = _clean_symbol(source_check.get("symbol") or source_check.get("exchange_symbol"))
        exchange_symbol = _exchange_symbol(source_check.get("exchange_symbol") or symbol)
        side = str(source_check.get("side") or "").lower()
        entry_price = _as_float(source_check.get("entry_price"))
        block_reasons: list[str] = []
        warnings: list[str] = []

        snapshot = self.store.get(symbol)
        if (snapshot is None or self._snapshot_is_not_ready(snapshot, observed_at)) and self.settings.allow_rest_fallback:
            try:
                ticker = self.provider.fetch_ticker(exchange_symbol)
                snapshot = self.store.update_from_ticker(symbol, ticker, observed_at=observed_at)
                self.store.save()
            except Exception as exc:
                block_reasons.append("rest_ticker_fallback_failed")
                warnings.append(str(exc))

        snapshot_details, snapshot_reasons = self._snapshot_details(snapshot, observed_at)
        block_reasons.extend(snapshot_reasons)

        order_book_details: dict[str, object] = {}
        if self.settings.enable_order_book_refresh:
            try:
                order_book = self.provider.fetch_order_book(exchange_symbol, self.settings.order_book_limit)
                order_book_details, ob_reasons = self._order_book_details(order_book, observed_at)
                block_reasons.extend(ob_reasons)
            except Exception as exc:
                block_reasons.append("order_book_refresh_failed")
                warnings.append(str(exc))

        exchange_time_details: dict[str, object] = {}
        try:
            server_time_ms = self.provider.fetch_exchange_time_ms()
            exchange_time_details, time_reasons = self._exchange_time_details(server_time_ms, observed_at)
            block_reasons.extend(time_reasons)
        except Exception as exc:
            block_reasons.append("exchange_time_fetch_failed")
            warnings.append(str(exc))

        diagnostics_details: dict[str, object] = {}
        if self.settings.require_clean_market_diagnostics:
            diagnostic = latest_market_diagnostic(
                diagnostics,
                symbol=symbol,
                timeframe=self.settings.diagnostics_timeframe,
            )
            diagnostics_reasons, diagnostics_details = market_diagnostics_blocks(diagnostic, self.settings, observed_at)
            block_reasons.extend(diagnostics_reasons)

        bid = _as_float(order_book_details.get("bid")) or _as_float(snapshot_details.get("bid"))
        ask = _as_float(order_book_details.get("ask")) or _as_float(snapshot_details.get("ask"))
        spread_bps = self._spread_bps(bid, ask)
        if spread_bps is None:
            block_reasons.append("current_bid_ask_missing")
        elif spread_bps > self.settings.max_spread_bps:
            block_reasons.append("spread_above_max")

        reference_price = self._reference_price(side, bid=bid, ask=ask, last=_as_float(snapshot_details.get("last")))
        entry_deviation_bps = None
        if entry_price <= 0 or reference_price <= 0:
            block_reasons.append("missing_entry_or_reference_price")
        else:
            entry_deviation_bps = _bps(entry_price - reference_price, reference_price)
            if entry_deviation_bps > self.settings.max_entry_price_deviation_bps:
                block_reasons.append("entry_price_deviation_above_max")

        unique_reasons = sorted(set(block_reasons))
        return {
            "source_request_id": source_check.get("source_request_id"),
            "source_client_order_id": source_check.get("source_client_order_id"),
            "source_intent_id": source_check.get("source_intent_id"),
            "symbol": symbol,
            "exchange_symbol": exchange_symbol,
            "side": side,
            "order_type": source_check.get("order_type"),
            "entry_price": entry_price,
            "reference_price": reference_price,
            "entry_price_deviation_bps": entry_deviation_bps,
            "spread_bps": spread_bps,
            "state": STATE_GUARD_READY if not unique_reasons else STATE_BLOCKED,
            "block_reasons": unique_reasons,
            "warnings": warnings,
            "snapshot": snapshot_details,
            "order_book": order_book_details,
            "exchange_time": exchange_time_details,
            "market_diagnostics": diagnostics_details,
            "paper_testnet_readiness_allowed": not unique_reasons,
            "order_submission_allowed": False,
            "live_execution_allowed": False,
        }

    def _snapshot_is_not_ready(self, snapshot: MarketSnapshot, observed_at: datetime) -> bool:
        details, reasons = self._snapshot_details(snapshot, observed_at)
        return bool(reasons or _as_float(details.get("age_seconds")) > self.settings.max_snapshot_age_seconds)

    def _snapshot_details(
        self,
        snapshot: MarketSnapshot | None,
        observed_at: datetime,
    ) -> tuple[dict[str, object], list[str]]:
        if snapshot is None:
            return {}, ["snapshot_missing"]
        reasons: list[str] = []
        bid_age = _age_seconds(snapshot.book_ticker_observed_at, observed_at)
        last_age = _age_seconds(snapshot.last_observed_at, observed_at)
        ages = [age for age in (bid_age, last_age) if age is not None]
        age_seconds = max(ages) if ages else None
        if bid_age is None:
            reasons.append("snapshot_bid_ask_timestamp_missing")
        if last_age is None:
            reasons.append("snapshot_last_timestamp_missing")
        if age_seconds is None:
            reasons.append("snapshot_timestamp_missing")
        elif age_seconds > self.settings.max_snapshot_age_seconds:
            reasons.append("snapshot_too_old")
        if snapshot.bid is None or snapshot.ask is None or snapshot.ask < snapshot.bid:
            reasons.append("snapshot_bid_ask_invalid")
        if snapshot.last is None:
            reasons.append("snapshot_last_missing")
        spread_bps = self._spread_bps(snapshot.bid, snapshot.ask)
        return (
            {
                **snapshot.to_dict(),
                "age_seconds": None if age_seconds is None else round(age_seconds, 3),
                "bid_ask_age_seconds": None if bid_age is None else round(bid_age, 3),
                "last_age_seconds": None if last_age is None else round(last_age, 3),
                "spread_bps": spread_bps,
            },
            reasons,
        )

    def _order_book_details(
        self,
        order_book: Mapping[str, object],
        observed_at: datetime,
    ) -> tuple[dict[str, object], list[str]]:
        if not order_book:
            return {}, ["order_book_missing"]
        bid = _best_order_book_side(order_book.get("bids"), 0)
        ask = _best_order_book_side(order_book.get("asks"), 0)
        bid_size = _best_order_book_side(order_book.get("bids"), 1)
        ask_size = _best_order_book_side(order_book.get("asks"), 1)
        reasons: list[str] = []
        if bid is None or ask is None or ask < bid:
            reasons.append("order_book_top_of_book_invalid")
        return (
            {
                "observed_at": observed_at.isoformat(),
                "bid": bid,
                "ask": ask,
                "bid_size": bid_size,
                "ask_size": ask_size,
                "spread_bps": self._spread_bps(bid, ask),
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

    @staticmethod
    def _spread_bps(bid: object, ask: object) -> float | None:
        bid_value = _as_float(bid)
        ask_value = _as_float(ask)
        if bid_value <= 0 or ask_value <= 0 or ask_value < bid_value:
            return None
        return _bps(ask_value - bid_value, (ask_value + bid_value) / 2.0)

    @staticmethod
    def _reference_price(side: str, *, bid: object, ask: object, last: object) -> float:
        if side == "buy":
            return _as_float(ask) or _as_float(last)
        if side == "sell":
            return _as_float(bid) or _as_float(last)
        return _as_float(last) or _as_float(ask) or _as_float(bid)

    def _summary(self, checks: Sequence[Mapping[str, object]]) -> dict[str, object]:
        blocked = [check for check in checks if check.get("state") == STATE_BLOCKED]
        ready = [check for check in checks if check.get("state") == STATE_GUARD_READY]
        reasons: dict[str, int] = {}
        for check in blocked:
            for reason in check.get("block_reasons", []):
                reasons[str(reason)] = reasons.get(str(reason), 0) + 1
        return {
            "requests_checked": len(checks),
            "final_guard_ready": len(ready),
            "blocked": len(blocked),
            "block_reasons": reasons,
            "paper_testnet_readiness_allowed": len(ready),
            "order_submission_allowed": 0,
            "live_execution_allowed": 0,
        }

    @staticmethod
    def _decision(summary: Mapping[str, object], source_reasons: Sequence[str]) -> dict[str, object]:
        if source_reasons:
            return {
                "action": "BLOCK_PHASE11_SOURCE",
                "readiness": "blocked",
                "reason": ",".join(source_reasons),
                "paper_testnet_readiness_allowed": False,
                "order_submission_allowed": False,
                "live_execution_allowed": False,
            }
        if _as_int(summary.get("requests_checked")) <= 0:
            return {
                "action": "COLLECT_PHASE11_MARKET_SAFETY",
                "readiness": "collecting",
                "reason": "no Phase 11 market-safe requests available for final pre-order guard",
                "paper_testnet_readiness_allowed": False,
                "order_submission_allowed": False,
                "live_execution_allowed": False,
            }
        if _as_int(summary.get("blocked")) > 0:
            return {
                "action": "BLOCK_FINAL_PRE_ORDER_GUARD",
                "readiness": "blocked",
                "reason": "one or more final realtime pre-order guard checks failed",
                "paper_testnet_readiness_allowed": False,
                "order_submission_allowed": False,
                "live_execution_allowed": False,
            }
        return {
            "action": "FINAL_PRE_ORDER_GUARD_READY",
            "readiness": "paper_testnet_ready",
            "reason": "fresh websocket/REST snapshot, spread, exchange time, entry deviation, and diagnostics checks passed",
            "paper_testnet_readiness_allowed": True,
            "order_submission_allowed": False,
            "live_execution_allowed": False,
        }

    def close(self) -> None:
        self.provider.close()


def _best_order_book_side(rows: object, index: int) -> float | None:
    if not isinstance(rows, list) or not rows:
        return None
    first = rows[0]
    if not isinstance(first, (list, tuple)) or len(first) <= index:
        return None
    return _positive_float(first[index])


def write_crypto_realtime_guard_outputs(report: Mapping[str, object], settings: CryptoRealtimeGuardSettings) -> None:
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
        "paper_testnet_readiness_allowed",
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
