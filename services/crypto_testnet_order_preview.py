from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence


PHASE10 = "phase10_testnet_order_preview"
STATE_REQUEST_PREVIEWED = "request_previewed"
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


def _clean_symbols(value: Sequence[str] | str) -> tuple[str, ...]:
    if isinstance(value, str):
        raw = value.split(",")
    else:
        raw = value
    return tuple(sorted({str(symbol).strip().upper().replace("/", "") for symbol in raw if str(symbol).strip()}))


def _exchange_symbol(order: Mapping[str, object]) -> str:
    symbol = str(order.get("exchange_symbol") or order.get("symbol") or "").strip().upper()
    if "/" in symbol:
        return symbol
    if symbol.endswith("USDT") and len(symbol) > 4:
        return f"{symbol[:-4]}/USDT"
    return symbol


def _phase10_order_id(order: Mapping[str, object], exchange_id: str) -> str:
    raw = "|".join(
        str(order.get(name, ""))
        for name in (
            "client_order_id",
            "intent_id",
            "journal_id",
            "symbol",
            "side",
            "order_type",
            "quantity",
            "entry_price",
        )
    )
    digest = hashlib.sha256(f"{exchange_id}|{raw}".encode("utf-8")).hexdigest()[:18]
    symbol = str(order.get("symbol") or "UNKNOWN").replace("/", "").upper()
    side = str(order.get("side") or "na").lower()
    return f"phase10-{symbol}-{side}-{digest}"[:64]


@dataclass(frozen=True)
class CryptoTestnetOrderPreviewSettings:
    phase9_report_path: Path | str = Path("reports/crypto_phase9_sandbox_execution_report.json")
    report_path: Path | str = Path("reports/crypto_phase10_testnet_order_preview.json")
    requests_csv_path: Path | str = Path("reports/crypto_phase10_testnet_order_requests.csv")
    exchange_id: str = "binance"
    default_type: str = "spot"
    sandbox_required: bool = True
    max_orders_per_run: int = 5
    require_phase9_ready: bool = True
    allowed_symbols: tuple[str, ...] | str = ("BTCUSDT", "ETHUSDT")
    require_spot_long_only: bool = True
    testnet_submission_enabled: bool = False
    allow_live_orders: bool = False

    def normalized(self) -> "CryptoTestnetOrderPreviewSettings":
        return CryptoTestnetOrderPreviewSettings(
            phase9_report_path=Path(self.phase9_report_path),
            report_path=Path(self.report_path),
            requests_csv_path=Path(self.requests_csv_path),
            exchange_id=str(self.exchange_id or "binance").strip().lower(),
            default_type=str(self.default_type or "spot").strip().lower(),
            sandbox_required=bool(self.sandbox_required),
            max_orders_per_run=max(0, int(self.max_orders_per_run)),
            require_phase9_ready=bool(self.require_phase9_ready),
            allowed_symbols=_clean_symbols(self.allowed_symbols),
            require_spot_long_only=bool(self.require_spot_long_only),
            testnet_submission_enabled=False,
            allow_live_orders=False,
        )


def load_phase9_sandbox_report(path: Path | str) -> dict[str, object]:
    report_path = Path(path)
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "type": "missing_crypto_sandbox_execution_report",
            "path": str(report_path),
            "orders": [],
            "live_execution_allowed": False,
            "decision": {
                "action": "MISSING_PHASE9_REPORT",
                "readiness": "missing",
                "reason": f"Phase 9 sandbox execution report missing: {report_path}",
                "live_execution_allowed": False,
                "sandbox_execution_ready": False,
            },
            "reconciliation": {"status": "missing", "live_order_sent": 0},
        }
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "type": "invalid_crypto_sandbox_execution_report",
            "path": str(report_path),
            "orders": [],
            "live_execution_allowed": False,
            "decision": {
                "action": "INVALID_PHASE9_REPORT",
                "readiness": "invalid",
                "reason": f"Phase 9 sandbox execution report unreadable: {exc}",
                "live_execution_allowed": False,
                "sandbox_execution_ready": False,
            },
            "reconciliation": {"status": "invalid", "live_order_sent": 0},
        }
    return payload if isinstance(payload, dict) else {}


def phase9_source_blocks(report: Mapping[str, object], settings: CryptoTestnetOrderPreviewSettings) -> list[str]:
    decision = _as_dict(report.get("decision"))
    reconciliation = _as_dict(report.get("reconciliation"))
    reasons: list[str] = []
    if report.get("live_execution_allowed") is True or decision.get("live_execution_allowed") is True:
        reasons.append("phase9_live_execution_allowed_true")
    if _as_int(reconciliation.get("live_order_sent")) > 0:
        reasons.append("phase9_report_has_live_order_sent")
    if str(reconciliation.get("status") or "").strip().lower() not in {"", "reconciled"}:
        reasons.append("phase9_reconciliation_not_clean")
    if settings.require_phase9_ready and decision.get("sandbox_execution_ready") is not True:
        reasons.append("phase9_sandbox_execution_not_ready")
    return reasons


def source_orders(report: Mapping[str, object], settings: CryptoTestnetOrderPreviewSettings) -> list[dict[str, object]]:
    raw_orders = report.get("orders") if isinstance(report.get("orders"), list) else []
    orders: list[dict[str, object]] = []
    for order in raw_orders:
        if not isinstance(order, Mapping):
            continue
        if order.get("accepted") is not True:
            continue
        if order.get("live_order_sent") is True:
            continue
        orders.append(dict(order))
    return orders[: settings.max_orders_per_run]


def order_blocks(order: Mapping[str, object], settings: CryptoTestnetOrderPreviewSettings) -> list[str]:
    symbol = str(order.get("symbol") or "").strip().upper().replace("/", "")
    side = str(order.get("side") or "").strip().upper()
    order_type = str(order.get("order_type") or "").strip().lower()
    reasons: list[str] = []
    if settings.allowed_symbols and symbol not in settings.allowed_symbols:
        reasons.append("symbol_not_allowed")
    if settings.require_spot_long_only and side != "BUY":
        reasons.append("spot_long_only_blocks_non_buy")
    if order_type not in {"market", "limit"}:
        reasons.append("unsupported_order_type")
    if _as_float(order.get("quantity")) <= 0:
        reasons.append("invalid_quantity")
    if order_type == "limit" and _as_float(order.get("entry_price")) <= 0:
        reasons.append("invalid_limit_price")
    if order.get("dry_run_only") is not True:
        reasons.append("source_order_not_dry_run_only")
    return reasons


def build_ccxt_order_request(order: Mapping[str, object], settings: CryptoTestnetOrderPreviewSettings) -> dict[str, object]:
    cfg = settings.normalized()
    order_type = str(order.get("order_type") or "").strip().lower()
    price = None if order_type == "market" else _as_float(order.get("entry_price"))
    client_order_id = _phase10_order_id(order, cfg.exchange_id)
    return {
        "request_id": client_order_id,
        "exchange_id": cfg.exchange_id,
        "default_type": cfg.default_type,
        "sandbox_required": cfg.sandbox_required,
        "method": "create_order",
        "symbol": _exchange_symbol(order),
        "type": order_type,
        "side": str(order.get("side") or "").strip().lower(),
        "amount": _as_float(order.get("quantity")),
        "price": price,
        "params": {
            "clientOrderId": client_order_id,
            "newClientOrderId": client_order_id,
            "test": True,
            "sandbox": True,
            "phase": PHASE10,
            "source_client_order_id": order.get("client_order_id"),
        },
    }


class CryptoTestnetOrderPreviewEngine:
    def __init__(self, settings: CryptoTestnetOrderPreviewSettings) -> None:
        self.settings = settings.normalized()

    def build_report(self, phase9_report: Mapping[str, object] | None = None) -> dict[str, object]:
        source = load_phase9_sandbox_report(self.settings.phase9_report_path) if phase9_report is None else dict(phase9_report)
        generated_at = utc_now()
        source_reasons = phase9_source_blocks(source, self.settings)
        requests: list[dict[str, object]] = []
        if not source_reasons:
            for order in source_orders(source, self.settings):
                requests.append(self._preview(order, generated_at))

        summary = self._summary(requests)
        decision = self._decision(summary, source_reasons)
        return {
            "type": "crypto_testnet_order_preview_report",
            "version": 1,
            "phase": PHASE10,
            "generated_at": generated_at,
            "decision": decision,
            "settings": {
                "phase9_report_path": str(self.settings.phase9_report_path),
                "exchange_id": self.settings.exchange_id,
                "default_type": self.settings.default_type,
                "sandbox_required": self.settings.sandbox_required,
                "max_orders_per_run": self.settings.max_orders_per_run,
                "require_phase9_ready": self.settings.require_phase9_ready,
                "allowed_symbols": list(self.settings.allowed_symbols),
                "require_spot_long_only": self.settings.require_spot_long_only,
                "testnet_submission_enabled": False,
                "allow_live_orders": False,
            },
            "source_report": {
                "type": source.get("type"),
                "phase": source.get("phase"),
                "generated_at": source.get("generated_at"),
                "decision": _as_dict(source.get("decision")),
                "reconciliation": _as_dict(source.get("reconciliation")),
                "live_execution_allowed": source.get("live_execution_allowed") is True,
                "block_reasons": source_reasons,
            },
            "summary": summary,
            "requests": requests,
            "order_submission_attempted": False,
            "testnet_order_submission_enabled": False,
            "live_execution_allowed": False,
            "safety_note": "Phase 10 builds CCXT-style sandbox request previews only. It does not call create_order or submit exchange orders.",
        }

    def _preview(self, order: Mapping[str, object], generated_at: str) -> dict[str, object]:
        reasons = order_blocks(order, self.settings)
        request = build_ccxt_order_request(order, self.settings) if not reasons else {}
        return {
            "source_client_order_id": order.get("client_order_id"),
            "source_intent_id": order.get("intent_id"),
            "symbol": order.get("symbol"),
            "exchange_symbol": order.get("exchange_symbol"),
            "side": order.get("side"),
            "order_type": order.get("order_type"),
            "quantity": order.get("quantity"),
            "entry_price": order.get("entry_price"),
            "notional_quote": _money(order.get("notional_quote")),
            "state": STATE_BLOCKED if reasons else STATE_REQUEST_PREVIEWED,
            "block_reasons": reasons,
            "request": request,
            "previewed_at": generated_at,
            "order_submission_attempted": False,
            "live_order_sent": False,
        }

    def _summary(self, requests: Sequence[Mapping[str, object]]) -> dict[str, object]:
        blocked = [request for request in requests if request.get("state") == STATE_BLOCKED]
        previewed = [request for request in requests if request.get("state") == STATE_REQUEST_PREVIEWED]
        reasons: dict[str, int] = {}
        for request in blocked:
            for reason in request.get("block_reasons", []):
                reasons[str(reason)] = reasons.get(str(reason), 0) + 1
        return {
            "source_orders_selected": len(requests),
            "request_previews": len(previewed),
            "blocked": len(blocked),
            "block_reasons": reasons,
            "order_submission_attempted": sum(1 for request in requests if _as_bool(request.get("order_submission_attempted"))),
            "live_order_sent": sum(1 for request in requests if request.get("live_order_sent") is True),
            "total_notional_quote": _money(sum(_as_float(request.get("notional_quote")) for request in previewed)),
        }

    def _decision(self, summary: Mapping[str, object], source_reasons: Sequence[str]) -> dict[str, object]:
        if source_reasons:
            return {
                "action": "BLOCK_PHASE9_SOURCE",
                "readiness": "blocked",
                "reason": ",".join(source_reasons),
                "live_execution_allowed": False,
                "testnet_request_preview_ready": False,
            }
        if _as_int(summary.get("order_submission_attempted")) > 0 or _as_int(summary.get("live_order_sent")) > 0:
            return {
                "action": "SAFETY_VIOLATION",
                "readiness": "blocked",
                "reason": "an order preview reported a submission attempt",
                "live_execution_allowed": False,
                "testnet_request_preview_ready": False,
            }
        if _as_int(summary.get("source_orders_selected")) <= 0:
            return {
                "action": "COLLECT_PHASE9_SANDBOX_ORDERS",
                "readiness": "collecting",
                "reason": "no accepted Phase 9 sandbox orders available for request preview",
                "live_execution_allowed": False,
                "testnet_request_preview_ready": False,
            }
        if _as_int(summary.get("blocked")) > 0:
            return {
                "action": "REVIEW_BLOCKED_TESTNET_REQUESTS",
                "readiness": "needs_review",
                "reason": "one or more Phase 10 request previews was blocked by safety validation",
                "live_execution_allowed": False,
                "testnet_request_preview_ready": False,
            }
        return {
            "action": "TESTNET_ORDER_REQUEST_PREVIEW_READY",
            "readiness": "preview_ready",
            "reason": "CCXT-style sandbox order requests are ready for manual testnet review",
            "live_execution_allowed": False,
            "testnet_request_preview_ready": True,
        }


def write_crypto_testnet_order_preview_outputs(
    report: Mapping[str, object],
    settings: CryptoTestnetOrderPreviewSettings,
) -> None:
    cfg = settings.normalized()
    report_path = Path(cfg.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")

    requests = report.get("requests") if isinstance(report.get("requests"), list) else []
    csv_path = Path(cfg.requests_csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "source_client_order_id",
        "source_intent_id",
        "symbol",
        "exchange_symbol",
        "side",
        "order_type",
        "quantity",
        "entry_price",
        "notional_quote",
        "state",
        "block_reasons",
        "request_id",
        "ccxt_symbol",
        "order_submission_attempted",
        "live_order_sent",
        "previewed_at",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for item in requests:
            if not isinstance(item, Mapping):
                continue
            request = _as_dict(item.get("request"))
            row = dict(item)
            row["block_reasons"] = ",".join(str(reason) for reason in item.get("block_reasons", []))
            row["request_id"] = request.get("request_id")
            row["ccxt_symbol"] = request.get("symbol")
            writer.writerow(row)
