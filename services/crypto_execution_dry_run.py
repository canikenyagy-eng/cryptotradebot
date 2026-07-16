from __future__ import annotations

import csv
import hashlib
import html
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from core.symbols import SymbolSpec, build_symbol_specs
from services.forward_outcomes import ForwardCandidate, load_candidates


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, str) and value.lower() == "inf":
        return float("inf")
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _money(value: object) -> float:
    return round(_as_float(value), 2)


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _intent_id(candidate: ForwardCandidate) -> str:
    raw = f"{candidate.journal_id}|{candidate.fingerprint}|{candidate.symbol}|{candidate.generated_at.isoformat()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _round_to_step(value: float, step: float | None, *, mode: str = "nearest") -> float:
    if step is None or step <= 0:
        return float(value)
    if mode == "down":
        return math.floor((value / step) + 1e-9) * step
    if mode == "up":
        return math.ceil(value / step) * step
    return round(value / step) * step


def _round_precision(value: float, precision: int | None) -> float:
    if precision is None:
        return float(value)
    return round(float(value), max(0, int(precision)))


def _price(value: float, spec: SymbolSpec) -> float:
    rounded = _round_to_step(value, spec.tick_size, mode="nearest")
    rounded = _round_precision(rounded, spec.price_precision)
    return round(float(rounded), 12)


def _quantity(value: float, spec: SymbolSpec) -> float:
    rounded = _round_to_step(value, spec.quantity_step, mode="down")
    rounded = _round_precision(rounded, spec.quantity_precision)
    return round(max(0.0, float(rounded)), 12)


@dataclass(frozen=True)
class CryptoExecutionDryRunSettings:
    journal_path: Path | str = Path("logs/crypto_forward_journal.jsonl")
    readiness_path: Path | str = Path("reports/crypto_phase7_paper_monitor.json")
    report_path: Path | str = Path("reports/crypto_phase8_execution_dry_run.json")
    tickets_csv_path: Path | str = Path("reports/crypto_phase8_order_intents.csv")
    dashboard_path: Path | str = Path("reports/crypto_phase8_execution_dry_run.html")
    sent_only: bool = True
    max_candidates: int = 10
    min_score: int = 0
    account_equity: float = 1000.0
    risk_mode: str = "fixed"
    risk_per_intent: float = 25.0
    risk_pct: float = 0.01
    min_notional_quote: float = 10.0
    max_notional_quote: float = 5000.0
    max_risk_quote: float = 50.0
    allow_spot_sell: bool = False
    require_phase7_ready: bool = True

    def normalized(self) -> "CryptoExecutionDryRunSettings":
        risk_mode = str(self.risk_mode or "fixed").strip().lower()
        if risk_mode not in {"fixed", "equity_pct"}:
            risk_mode = "fixed"
        return CryptoExecutionDryRunSettings(
            journal_path=Path(self.journal_path),
            readiness_path=Path(self.readiness_path),
            report_path=Path(self.report_path),
            tickets_csv_path=Path(self.tickets_csv_path),
            dashboard_path=Path(self.dashboard_path),
            sent_only=bool(self.sent_only),
            max_candidates=max(1, int(self.max_candidates)),
            min_score=max(0, min(100, int(self.min_score))),
            account_equity=max(0.0, float(self.account_equity)),
            risk_mode=risk_mode,
            risk_per_intent=max(0.0, float(self.risk_per_intent)),
            risk_pct=max(0.0, min(1.0, float(self.risk_pct))),
            min_notional_quote=max(0.0, float(self.min_notional_quote)),
            max_notional_quote=max(0.0, float(self.max_notional_quote)),
            max_risk_quote=max(0.0, float(self.max_risk_quote)),
            allow_spot_sell=bool(self.allow_spot_sell),
            require_phase7_ready=bool(self.require_phase7_ready),
        )


class CryptoExecutionDryRunPlanner:
    def __init__(
        self,
        settings: CryptoExecutionDryRunSettings,
        *,
        symbol_specs: Mapping[str, object] | None = None,
        market_type: str = "crypto_spot",
        pairs: Iterable[str] = (),
    ) -> None:
        self.settings = settings.normalized()
        self.market_type = str(market_type or "crypto_spot").strip().lower()
        self.symbol_specs = build_symbol_specs(pairs, raw_specs=symbol_specs, market_type=self.market_type)

    def build_report(self) -> dict[str, object]:
        readiness = self._read_readiness()
        candidates = self._load_candidates()
        intents = [self._intent(candidate, readiness) for candidate in candidates]
        summary = self._summary(intents)
        decision = self._decision(summary, readiness)
        return {
            "type": "crypto_execution_dry_run_report",
            "version": 1,
            "phase": "phase8_execution_design_dry_run",
            "generated_at": utc_now(),
            "decision": decision,
            "settings": {
                "journal_path": str(self.settings.journal_path),
                "readiness_path": str(self.settings.readiness_path),
                "sent_only": self.settings.sent_only,
                "max_candidates": self.settings.max_candidates,
                "min_score": self.settings.min_score,
                "account_equity": _money(self.settings.account_equity),
                "risk_mode": self.settings.risk_mode,
                "risk_per_intent": _money(self.settings.risk_per_intent),
                "risk_pct": self.settings.risk_pct,
                "min_notional_quote": _money(self.settings.min_notional_quote),
                "max_notional_quote": _money(self.settings.max_notional_quote),
                "max_risk_quote": _money(self.settings.max_risk_quote),
                "allow_spot_sell": self.settings.allow_spot_sell,
                "require_phase7_ready": self.settings.require_phase7_ready,
                "market_type": self.market_type,
            },
            "summary": summary,
            "phase7_readiness": readiness,
            "intents": intents,
            "live_execution_allowed": False,
            "safety_note": "Phase 8 creates dry-run order intents only. It does not submit exchange orders.",
        }

    def _load_candidates(self) -> list[ForwardCandidate]:
        candidates = load_candidates(self.settings.journal_path, sent_only=self.settings.sent_only)
        candidates = [candidate for candidate in candidates if candidate.score >= self.settings.min_score]
        candidates.sort(key=lambda item: (item.generated_at, item.symbol, item.journal_id), reverse=True)
        return candidates[: self.settings.max_candidates]

    def _read_readiness(self) -> dict[str, object]:
        path = Path(self.settings.readiness_path)
        if not path.exists():
            return {
                "present": False,
                "next_phase_allowed": False,
                "readiness": "missing",
                "action": "MISSING_PHASE7_REPORT",
                "reason": f"readiness report missing: {path}",
            }
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "present": False,
                "next_phase_allowed": False,
                "readiness": "invalid",
                "action": "INVALID_PHASE7_REPORT",
                "reason": f"readiness report unreadable: {exc}",
            }
        if not isinstance(payload, dict):
            payload = {}
        decision = _as_dict(payload.get("decision"))
        return {
            "present": True,
            "next_phase_allowed": decision.get("next_phase_allowed") is True,
            "readiness": decision.get("readiness", ""),
            "action": decision.get("action", ""),
            "reason": decision.get("reason", ""),
            "generated_at": payload.get("generated_at"),
        }

    def _risk_quote(self) -> float:
        if self.settings.risk_mode == "equity_pct":
            return self.settings.account_equity * self.settings.risk_pct
        return self.settings.risk_per_intent

    def _intent(self, candidate: ForwardCandidate, readiness: Mapping[str, object]) -> dict[str, object]:
        spec = self.symbol_specs.get(candidate.symbol) or SymbolSpec.from_symbol(
            candidate.symbol,
            market_type=self.market_type,
        )
        block_reasons: list[str] = []
        warnings: list[str] = []
        adjustments: list[str] = []

        if self.settings.require_phase7_ready and readiness.get("next_phase_allowed") is not True:
            block_reasons.append("phase7_not_ready")
        if self.market_type == "crypto_spot" and candidate.side == "SELL" and not self.settings.allow_spot_sell:
            block_reasons.append("spot_sell_intent_blocked")

        raw_entry = float(candidate.entry)
        raw_stop = float(candidate.stop_loss)
        raw_take_profit = float(candidate.take_profit)
        entry = _price(raw_entry, spec)
        stop_loss = _price(raw_stop, spec)
        take_profit = _price(raw_take_profit, spec)
        for name, raw, rounded in (
            ("entry", raw_entry, entry),
            ("stop_loss", raw_stop, stop_loss),
            ("take_profit", raw_take_profit, take_profit),
        ):
            if abs(raw - rounded) > 1e-12:
                adjustments.append(f"{name}_rounded_to_tick")

        risk_per_unit = abs(entry - stop_loss)
        if risk_per_unit <= 0:
            block_reasons.append("invalid_stop_distance")

        risk_quote = min(self._risk_quote(), self.settings.max_risk_quote) if self.settings.max_risk_quote > 0 else self._risk_quote()
        if self._risk_quote() > self.settings.max_risk_quote > 0:
            warnings.append("risk_capped_by_max_risk_quote")

        raw_quantity = 0.0 if risk_per_unit <= 0 else risk_quote / risk_per_unit
        quantity = _quantity(raw_quantity, spec)
        notional = quantity * entry
        estimated_fee = notional * float(spec.taker_fee_rate or 0.0)

        if spec.min_order_size is not None and quantity < spec.min_order_size:
            block_reasons.append("quantity_below_min_order_size")
        if notional < self.settings.min_notional_quote:
            block_reasons.append("notional_below_minimum")
        if self.settings.max_notional_quote > 0 and notional > self.settings.max_notional_quote:
            block_reasons.append("notional_above_maximum")
        if quantity <= 0:
            block_reasons.append("quantity_zero")

        signal = _as_dict(candidate.candidate_event.get("signal"))
        order_type = "market" if candidate.entry_mode.upper() == "MARKET" else "limit"
        return {
            "intent_id": _intent_id(candidate),
            "journal_id": candidate.journal_id,
            "fingerprint": candidate.fingerprint,
            "symbol": candidate.symbol,
            "exchange_symbol": spec.exchange_or_symbol,
            "side": candidate.side,
            "order_type": order_type,
            "dry_run_only": True,
            "status": "ready_dry_run" if not block_reasons else "blocked",
            "block_reasons": block_reasons,
            "warnings": warnings,
            "adjustments": adjustments,
            "generated_at": candidate.generated_at.isoformat(),
            "score": candidate.score,
            "entry_price": entry,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk_per_unit": round(risk_per_unit, 12),
            "risk_quote": _money(risk_quote),
            "quantity": quantity,
            "notional_quote": _money(notional),
            "estimated_taker_fee_quote": _money(estimated_fee),
            "tick_size": spec.tick_size,
            "min_order_size": spec.min_order_size,
            "quantity_step": spec.quantity_step,
            "regime_label": signal.get("regime_label"),
            "trigger_event": signal.get("trigger_event"),
            "zone": signal.get("zone"),
        }

    def _summary(self, intents: list[Mapping[str, object]]) -> dict[str, object]:
        ready = [intent for intent in intents if intent.get("status") == "ready_dry_run"]
        blocked = [intent for intent in intents if intent.get("status") == "blocked"]
        reasons: dict[str, int] = {}
        for intent in blocked:
            for reason in intent.get("block_reasons", []):
                reasons[str(reason)] = reasons.get(str(reason), 0) + 1
        return {
            "candidates": len(intents),
            "ready_dry_run": len(ready),
            "blocked": len(blocked),
            "total_notional_quote": _money(sum(_as_float(intent.get("notional_quote")) for intent in ready)),
            "total_risk_quote": _money(sum(_as_float(intent.get("risk_quote")) for intent in ready)),
            "block_reasons": reasons,
        }

    def _decision(self, summary: Mapping[str, object], readiness: Mapping[str, object]) -> dict[str, object]:
        if self.settings.require_phase7_ready and readiness.get("next_phase_allowed") is not True:
            return {
                "action": "WAIT_FOR_PHASE7_READINESS",
                "readiness": "blocked",
                "reason": str(readiness.get("reason") or "Phase 7 readiness has not passed"),
                "live_execution_allowed": False,
                "dry_run_intents_ready": False,
            }
        if _as_int(summary.get("candidates")) <= 0:
            return {
                "action": "COLLECT_FORWARD_SIGNALS",
                "readiness": "collecting",
                "reason": "no eligible forward signal candidates found",
                "live_execution_allowed": False,
                "dry_run_intents_ready": False,
            }
        if _as_int(summary.get("ready_dry_run")) <= 0:
            return {
                "action": "REVIEW_BLOCKED_ORDER_INTENTS",
                "readiness": "needs_review",
                "reason": "all generated order intents are blocked by dry-run preflight rules",
                "live_execution_allowed": False,
                "dry_run_intents_ready": False,
            }
        return {
            "action": "DRY_RUN_ORDER_INTENTS_READY",
            "readiness": "dry_run_ready",
            "reason": "one or more dry-run order intents passed preflight validation",
            "live_execution_allowed": False,
            "dry_run_intents_ready": True,
        }


def write_crypto_execution_dry_run_outputs(report: Mapping[str, object], settings: CryptoExecutionDryRunSettings) -> None:
    cfg = settings.normalized()
    report_path = Path(cfg.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")

    intents = report.get("intents") if isinstance(report.get("intents"), list) else []
    csv_path = Path(cfg.tickets_csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "intent_id",
        "journal_id",
        "symbol",
        "exchange_symbol",
        "side",
        "order_type",
        "status",
        "block_reasons",
        "score",
        "entry_price",
        "stop_loss",
        "take_profit",
        "risk_quote",
        "quantity",
        "notional_quote",
        "estimated_taker_fee_quote",
        "dry_run_only",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for intent in intents:
            row = dict(intent) if isinstance(intent, Mapping) else {}
            if isinstance(row.get("block_reasons"), list):
                row["block_reasons"] = ",".join(str(item) for item in row["block_reasons"])
            writer.writerow(row)

    dashboard_path = Path(cfg.dashboard_path)
    dashboard_path.parent.mkdir(parents=True, exist_ok=True)
    dashboard_path.write_text(render_crypto_execution_dry_run_dashboard(report), encoding="utf-8")


def render_crypto_execution_dry_run_dashboard(report: Mapping[str, object]) -> str:
    decision = _as_dict(report.get("decision"))
    summary = _as_dict(report.get("summary"))
    settings = _as_dict(report.get("settings"))
    intents = [intent for intent in report.get("intents", []) if isinstance(intent, Mapping)]
    status_class = "ok" if decision.get("dry_run_intents_ready") is True else ("warn" if decision.get("readiness") == "collecting" else "bad")
    rows = []
    for intent in intents:
        rows.append(
            "<tr>"
            f"<td>{_escape(intent.get('symbol', ''))}</td>"
            f"<td>{_escape(intent.get('side', ''))}</td>"
            f"<td>{_escape(intent.get('status', ''))}</td>"
            f"<td>{_as_int(intent.get('score'))}</td>"
            f"<td>{_escape(intent.get('quantity', ''))}</td>"
            f"<td>{_money(intent.get('notional_quote')):.2f}</td>"
            f"<td>{_escape(','.join(str(item) for item in intent.get('block_reasons', [])))}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="7" class="empty">No dry-run order intents</td></tr>')

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Crypto Execution Dry Run</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: Canvas;
      --fg: CanvasText;
      --muted: color-mix(in srgb, CanvasText 62%, Canvas);
      --panel: color-mix(in srgb, Canvas 92%, CanvasText);
      --border: color-mix(in srgb, CanvasText 18%, Canvas);
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--fg); font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.45; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 24px; }}
    header {{ display: grid; grid-template-columns: 1fr auto; gap: 16px; align-items: start; border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 16px; }}
    h1, h2 {{ margin: 0; font-weight: 600; letter-spacing: 0; }}
    h1 {{ font-size: 24px; }}
    h2 {{ font-size: 16px; margin-bottom: 10px; }}
    .subtle {{ color: var(--muted); font-size: 13px; margin-top: 4px; }}
    .badge {{ display: inline-flex; align-items: center; min-height: 28px; border: 1px solid var(--border); border-radius: 6px; padding: 4px 10px; font-size: 13px; font-weight: 600; white-space: nowrap; }}
    .badge.ok {{ outline: 2px solid color-mix(in srgb, CanvasText 25%, Canvas); }}
    .badge.warn {{ outline: 2px dashed color-mix(in srgb, CanvasText 25%, Canvas); }}
    .badge.bad {{ outline: 2px double color-mix(in srgb, CanvasText 25%, Canvas); }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 14px; }}
    .metric, .panel {{ border: 1px solid var(--border); border-radius: 8px; background: var(--panel); }}
    .metric {{ padding: 12px; min-height: 86px; }}
    .metric div {{ color: var(--muted); font-size: 13px; }}
    .metric strong {{ display: block; font-size: 24px; margin-top: 4px; }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; margin-top: 4px; }}
    .panel {{ padding: 14px; overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ text-align: left; border-bottom: 1px solid var(--border); padding: 8px 6px; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 600; }}
    tr:last-child td {{ border-bottom: 0; }}
    .empty {{ color: var(--muted); text-align: center; }}
    @media (max-width: 760px) {{ main {{ padding: 16px; }} header, .grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Crypto Execution Dry Run</h1>
        <div class="subtle">Generated {_escape(report.get("generated_at", ""))}</div>
      </div>
      <span class="badge {status_class}">{_escape(decision.get("action", "UNKNOWN"))}</span>
    </header>
    <section class="grid">
      {_metric("Candidates", _as_int(summary.get("candidates")), f"sent_only {_escape(settings.get('sent_only', ''))}")}
      {_metric("Ready Intents", _as_int(summary.get("ready_dry_run")), f"blocked {_as_int(summary.get('blocked'))}")}
      {_metric("Total Risk", f"{_money(summary.get('total_risk_quote')):.2f}", f"risk mode {_escape(settings.get('risk_mode', ''))}")}
      {_metric("Live Orders", _escape(report.get("live_execution_allowed", False)), "dry-run only")}
    </section>
    <section class="panel">
      <h2>Decision</h2>
      <table><tbody>
        <tr><th>Readiness</th><td>{_escape(decision.get("readiness", ""))}</td></tr>
        <tr><th>Reason</th><td>{_escape(decision.get("reason", ""))}</td></tr>
        <tr><th>Safety</th><td>{_escape(report.get("safety_note", ""))}</td></tr>
      </tbody></table>
    </section>
    <section class="panel">
      <h2>Order Intents</h2>
      <table><thead><tr><th>Symbol</th><th>Side</th><th>Status</th><th>Score</th><th>Qty</th><th>Notional</th><th>Blocks</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
    </section>
  </main>
</body>
</html>
"""


def _metric(label: str, value: object, detail: str = "") -> str:
    detail_html = f"<span>{_escape(detail)}</span>" if detail else ""
    return f'<div class="metric"><div>{_escape(label)}</div><strong>{_escape(value)}</strong>{detail_html}</div>'
