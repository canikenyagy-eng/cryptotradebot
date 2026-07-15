from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence


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


def _fmt_number(value: object, digits: int = 2) -> str:
    numeric = _as_float(value)
    if numeric == float("inf"):
        return "inf"
    return f"{numeric:.{digits}f}"


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _guardrail(
    name: str,
    passed: bool,
    severity: str,
    reason: str,
    details: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        "name": name,
        "passed": bool(passed),
        "severity": severity,
        "reason": reason,
        "details": dict(details or {}),
    }


@dataclass(frozen=True)
class CryptoExecutionReadinessThresholds:
    min_paper_trades: int = 30
    min_symbol_trades: int = 5
    min_avg_r: float = 0.10
    min_profit_factor: float = 1.20
    max_drawdown_pct: float = 10.0
    min_roi_pct: float = 0.0
    required_symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
    require_health_ok: bool = True
    require_signal_live_mode_disabled: bool = True

    def normalized(self) -> "CryptoExecutionReadinessThresholds":
        return CryptoExecutionReadinessThresholds(
            min_paper_trades=max(1, int(self.min_paper_trades)),
            min_symbol_trades=max(1, int(self.min_symbol_trades)),
            min_avg_r=float(self.min_avg_r),
            min_profit_factor=max(0.0, float(self.min_profit_factor)),
            max_drawdown_pct=max(0.0, float(self.max_drawdown_pct)),
            min_roi_pct=float(self.min_roi_pct),
            required_symbols=tuple(sorted({str(symbol).strip().upper() for symbol in self.required_symbols if str(symbol).strip()})),
            require_health_ok=bool(self.require_health_ok),
            require_signal_live_mode_disabled=bool(self.require_signal_live_mode_disabled),
        )


def _symbol_guardrail(
    by_symbol: Mapping[str, object],
    thresholds: CryptoExecutionReadinessThresholds,
) -> dict[str, object]:
    missing: list[str] = []
    undersampled: dict[str, int] = {}
    negative: dict[str, float] = {}
    for symbol in thresholds.required_symbols:
        row = _as_dict(by_symbol.get(symbol))
        trades = _as_int(row.get("trades"))
        avg_r = _as_float(row.get("avg_r"))
        if trades <= 0:
            missing.append(symbol)
            continue
        if trades < thresholds.min_symbol_trades:
            undersampled[symbol] = trades
        if avg_r < 0.0 and trades >= thresholds.min_symbol_trades:
            negative[symbol] = avg_r

    passed = not missing and not undersampled and not negative
    if missing:
        reason = f"missing paper trades for {', '.join(missing)}"
        severity = "collecting"
    elif undersampled:
        reason = f"symbol sample below minimum {thresholds.min_symbol_trades}"
        severity = "collecting"
    elif negative:
        reason = "one or more required symbols has negative paper expectancy"
        severity = "blocker"
    else:
        reason = "required symbols have enough non-negative paper evidence"
        severity = "info"

    return _guardrail(
        "required_symbol_coverage",
        passed,
        severity,
        reason,
        {
            "required_symbols": list(thresholds.required_symbols),
            "missing": missing,
            "undersampled": undersampled,
            "negative_avg_r": negative,
        },
    )


def _market_scope_guardrail(
    settings_snapshot: Mapping[str, object],
    thresholds: CryptoExecutionReadinessThresholds,
) -> dict[str, object]:
    market_type = str(settings_snapshot.get("market_type", "")).strip().lower()
    data_source = str(settings_snapshot.get("data_source", "")).strip().lower()
    pairs = {str(pair).strip().upper() for pair in settings_snapshot.get("pairs", []) if str(pair).strip()}
    missing = [symbol for symbol in thresholds.required_symbols if symbol not in pairs]
    passed = market_type == "crypto_spot" and data_source == "ccxt" and not missing
    reason = (
        "crypto CCXT BTC/ETH scope is active"
        if passed
        else "Phase 7 expects MARKET_TYPE=crypto_spot, DATA_SOURCE=ccxt, and required crypto pairs"
    )
    return _guardrail(
        "crypto_market_scope",
        passed,
        "blocker" if not passed else "info",
        reason,
        {
            "market_type": market_type,
            "data_source": data_source,
            "pairs": sorted(pairs),
            "missing_required_symbols": missing,
        },
    )


def _signal_live_mode_guardrail(
    settings_snapshot: Mapping[str, object],
    thresholds: CryptoExecutionReadinessThresholds,
) -> dict[str, object]:
    enabled = bool(settings_snapshot.get("enable_live_mode"))
    if not thresholds.require_signal_live_mode_disabled:
        return _guardrail(
            "signal_live_mode_disabled",
            True,
            "info",
            "signal live mode check disabled by monitor settings",
            {"enable_live_mode": enabled},
        )
    passed = not enabled
    return _guardrail(
        "signal_live_mode_disabled",
        passed,
        "blocker" if not passed else "info",
        "ENABLE_LIVE_MODE is disabled" if passed else "ENABLE_LIVE_MODE must stay disabled before execution design",
        {"enable_live_mode": enabled, "live_mode": settings_snapshot.get("live_mode")},
    )


def _health_guardrail(health: Mapping[str, object], thresholds: CryptoExecutionReadinessThresholds) -> dict[str, object]:
    if not thresholds.require_health_ok:
        return _guardrail("feed_and_heartbeat_health", True, "info", "health check disabled by monitor settings", {})
    passed = bool(health.get("ok")) is True
    reason = "feed and heartbeat health are OK" if passed else str(health.get("reason") or "feed or heartbeat health is not OK")
    return _guardrail(
        "feed_and_heartbeat_health",
        passed,
        "blocker" if not passed else "info",
        reason,
        {
            "status": health.get("status"),
            "age_seconds": health.get("age_seconds"),
            "max_age_seconds": health.get("max_age_seconds"),
        },
    )


def _paper_guardrails(
    paper_report: Mapping[str, object],
    thresholds: CryptoExecutionReadinessThresholds,
) -> list[dict[str, object]]:
    overall = _as_dict(paper_report.get("overall"))
    executed = _as_int(overall.get("executed_trades"))
    avg_r = _as_float(overall.get("avg_r"))
    profit_factor = _as_float(overall.get("profit_factor"))
    drawdown_pct = _as_float(overall.get("max_drawdown_pct"))
    roi_pct = _as_float(overall.get("roi_pct"))

    sample_passed = executed >= thresholds.min_paper_trades
    avg_passed = avg_r >= thresholds.min_avg_r
    pf_passed = profit_factor >= thresholds.min_profit_factor
    dd_passed = drawdown_pct <= thresholds.max_drawdown_pct
    roi_passed = roi_pct >= thresholds.min_roi_pct

    return [
        _guardrail(
            "paper_sample_size",
            sample_passed,
            "collecting" if not sample_passed else "info",
            (
                f"executed paper trades {executed} >= minimum {thresholds.min_paper_trades}"
                if sample_passed
                else f"executed paper trades {executed} < minimum {thresholds.min_paper_trades}"
            ),
            {"executed_trades": executed, "minimum": thresholds.min_paper_trades},
        ),
        _guardrail(
            "paper_expectancy",
            avg_passed and pf_passed,
            "blocker" if not (avg_passed and pf_passed) and sample_passed else "collecting",
            (
                "paper expectancy meets AvgR and profit-factor thresholds"
                if avg_passed and pf_passed
                else "paper expectancy is below required AvgR or profit-factor threshold"
            ),
            {
                "avg_r": avg_r,
                "min_avg_r": thresholds.min_avg_r,
                "profit_factor": overall.get("profit_factor"),
                "min_profit_factor": thresholds.min_profit_factor,
            },
        ),
        _guardrail(
            "paper_drawdown",
            dd_passed,
            "blocker" if not dd_passed and sample_passed else "collecting",
            (
                "paper drawdown is inside the readiness limit"
                if dd_passed
                else "paper drawdown is above the readiness limit"
            ),
            {"max_drawdown_pct": drawdown_pct, "limit": thresholds.max_drawdown_pct},
        ),
        _guardrail(
            "paper_roi",
            roi_passed,
            "blocker" if not roi_passed and sample_passed else "collecting",
            "paper ROI is non-negative enough for readiness" if roi_passed else "paper ROI is below readiness threshold",
            {"roi_pct": roi_pct, "minimum": thresholds.min_roi_pct},
        ),
    ]


def _decision(guardrails: Sequence[Mapping[str, object]]) -> dict[str, object]:
    failed = [guard for guard in guardrails if guard.get("passed") is not True]
    blockers = [guard for guard in failed if guard.get("severity") == "blocker"]
    collecting = [guard for guard in failed if guard.get("severity") == "collecting"]

    if blockers:
        return {
            "action": "BLOCK_LIVE_EXECUTION_DESIGN",
            "readiness": "blocked",
            "reason": str(blockers[0].get("reason") or "a blocker guardrail failed"),
            "live_execution_allowed": False,
            "next_phase_allowed": False,
        }
    if collecting:
        return {
            "action": "KEEP_PAPER_MONITORING",
            "readiness": "collecting",
            "reason": str(collecting[0].get("reason") or "more paper-trading evidence is required"),
            "live_execution_allowed": False,
            "next_phase_allowed": False,
        }
    return {
        "action": "READY_FOR_EXECUTION_DESIGN_REVIEW",
        "readiness": "paper_ready_for_execution_design",
        "reason": "all Phase 7 paper and health guardrails pass; live orders remain disabled",
        "live_execution_allowed": False,
        "next_phase_allowed": True,
    }


def build_crypto_execution_readiness_report(
    *,
    paper_report: Mapping[str, object],
    health: Mapping[str, object] | None = None,
    thresholds: CryptoExecutionReadinessThresholds | None = None,
    settings_snapshot: Mapping[str, object] | None = None,
    outcome_update: Mapping[str, object] | None = None,
    generated_paths: Mapping[str, object] | None = None,
) -> dict[str, object]:
    threshold_values = (thresholds or CryptoExecutionReadinessThresholds()).normalized()
    settings_payload = dict(settings_snapshot or {})
    health_payload = _as_dict(health)
    by_symbol = _as_dict(paper_report.get("by_symbol"))
    guardrails = [
        _market_scope_guardrail(settings_payload, threshold_values),
        _signal_live_mode_guardrail(settings_payload, threshold_values),
        _guardrail(
            "live_order_execution_absent",
            True,
            "info",
            "Phase 7 does not include exchange account keys, balances, order routing, or order placement",
            {},
        ),
        _health_guardrail(health_payload, threshold_values),
        *_paper_guardrails(paper_report, threshold_values),
        _symbol_guardrail(by_symbol, threshold_values),
    ]
    decision = _decision(guardrails)
    overall = _as_dict(paper_report.get("overall"))

    return {
        "type": "crypto_execution_readiness_report",
        "version": 1,
        "generated_at": utc_now(),
        "phase": "phase7_crypto_paper_monitor",
        "decision": decision,
        "thresholds": {
            "min_paper_trades": threshold_values.min_paper_trades,
            "min_symbol_trades": threshold_values.min_symbol_trades,
            "min_avg_r": threshold_values.min_avg_r,
            "min_profit_factor": threshold_values.min_profit_factor,
            "max_drawdown_pct": threshold_values.max_drawdown_pct,
            "min_roi_pct": threshold_values.min_roi_pct,
            "required_symbols": list(threshold_values.required_symbols),
            "require_health_ok": threshold_values.require_health_ok,
            "require_signal_live_mode_disabled": threshold_values.require_signal_live_mode_disabled,
        },
        "guardrails": guardrails,
        "paper_summary": {
            "candidates": _as_int(overall.get("candidates")),
            "executed_trades": _as_int(overall.get("executed_trades")),
            "skipped": _as_int(overall.get("skipped")),
            "win_rate": _as_float(overall.get("win_rate")),
            "avg_r": _as_float(overall.get("avg_r")),
            "total_r": _as_float(overall.get("total_r")),
            "profit_factor": overall.get("profit_factor", 0.0),
            "final_balance": _as_float(overall.get("final_balance")),
            "net_pnl": _as_float(overall.get("net_pnl")),
            "roi_pct": _as_float(overall.get("roi_pct")),
            "max_drawdown": _as_float(overall.get("max_drawdown")),
            "max_drawdown_pct": _as_float(overall.get("max_drawdown_pct")),
        },
        "paper_by_symbol": by_symbol,
        "health": health_payload,
        "settings_snapshot": settings_payload,
        "outcome_update": dict(outcome_update or {}),
        "paths": dict(generated_paths or {}),
    }


def render_crypto_execution_readiness_dashboard(report: Mapping[str, object]) -> str:
    decision = _as_dict(report.get("decision"))
    paper = _as_dict(report.get("paper_summary"))
    guardrails = [guard for guard in report.get("guardrails", []) if isinstance(guard, Mapping)]
    by_symbol = _as_dict(report.get("paper_by_symbol"))
    paths = _as_dict(report.get("paths"))
    status_class = "ok" if decision.get("next_phase_allowed") is True else ("warn" if decision.get("readiness") == "collecting" else "bad")

    guard_rows = []
    for guard in guardrails:
        guard_rows.append(
            "<tr>"
            f"<td>{_escape(guard.get('name', ''))}</td>"
            f"<td>{'PASS' if guard.get('passed') is True else 'WAIT'}</td>"
            f"<td>{_escape(guard.get('severity', ''))}</td>"
            f"<td>{_escape(guard.get('reason', ''))}</td>"
            "</tr>"
        )
    if not guard_rows:
        guard_rows.append('<tr><td colspan="4" class="empty">No guardrails evaluated</td></tr>')

    symbol_rows = []
    for symbol, raw in sorted(by_symbol.items()):
        row = _as_dict(raw)
        symbol_rows.append(
            "<tr>"
            f"<td>{_escape(symbol)}</td>"
            f"<td>{_as_int(row.get('trades'))}</td>"
            f"<td>{_fmt_number(_as_float(row.get('win_rate')) * 100, 1)}%</td>"
            f"<td>{_fmt_number(row.get('avg_r'), 3)}</td>"
            f"<td>{_escape(row.get('profit_factor', 0.0))}</td>"
            f"<td>{_fmt_number(row.get('net_pnl'), 2)}</td>"
            "</tr>"
        )
    if not symbol_rows:
        symbol_rows.append('<tr><td colspan="6" class="empty">No executed paper trades yet</td></tr>')

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Crypto Execution Readiness</title>
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
    .panels {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
    .panel {{ padding: 14px; overflow-x: auto; }}
    .panel.wide {{ grid-column: 1 / -1; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ text-align: left; border-bottom: 1px solid var(--border); padding: 8px 6px; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 600; }}
    tr:last-child td {{ border-bottom: 0; }}
    .empty {{ color: var(--muted); text-align: center; }}
    @media (max-width: 760px) {{ main {{ padding: 16px; }} header, .grid, .panels {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Crypto Execution Readiness</h1>
        <div class="subtle">Generated {_escape(report.get("generated_at", ""))}</div>
      </div>
      <span class="badge {status_class}">{_escape(decision.get("action", "UNKNOWN"))}</span>
    </header>
    <section class="grid">
      {_metric("Executed Trades", _as_int(paper.get("executed_trades")), f"candidates {_as_int(paper.get('candidates'))}")}
      {_metric("Final Balance", _fmt_number(paper.get("final_balance"), 2), f"net {_fmt_number(paper.get('net_pnl'), 2)}")}
      {_metric("Avg R", _fmt_number(paper.get("avg_r"), 3), f"PF {_escape(paper.get('profit_factor', 0.0))}")}
      {_metric("Max Drawdown", f"{_fmt_number(paper.get('max_drawdown_pct'), 2)}%", f"ROI {_fmt_number(paper.get('roi_pct'), 2)}%")}
    </section>
    <section class="panel wide">
      <h2>Decision</h2>
      <table><tbody>
        <tr><th>Readiness</th><td>{_escape(decision.get("readiness", ""))}</td></tr>
        <tr><th>Live execution allowed</th><td>{_escape(decision.get("live_execution_allowed", False))}</td></tr>
        <tr><th>Reason</th><td>{_escape(decision.get("reason", ""))}</td></tr>
        <tr><th>Paper report</th><td>{_escape(paths.get("paper_report", ""))}</td></tr>
      </tbody></table>
    </section>
    <section class="panels">
      <section class="panel wide">
        <h2>Guardrails</h2>
        <table><thead><tr><th>Name</th><th>Status</th><th>Severity</th><th>Reason</th></tr></thead><tbody>{''.join(guard_rows)}</tbody></table>
      </section>
      <section class="panel wide">
        <h2>Paper By Symbol</h2>
        <table><thead><tr><th>Symbol</th><th>Trades</th><th>Win</th><th>AvgR</th><th>PF</th><th>Net</th></tr></thead><tbody>{''.join(symbol_rows)}</tbody></table>
      </section>
    </section>
  </main>
</body>
</html>
"""


def _metric(label: str, value: object, detail: str = "") -> str:
    detail_html = f"<span>{_escape(detail)}</span>" if detail else ""
    return f'<div class="metric"><div>{_escape(label)}</div><strong>{_escape(value)}</strong>{detail_html}</div>'


def write_crypto_execution_readiness_outputs(
    report: Mapping[str, object],
    *,
    report_path: Path | str,
    dashboard_path: Path | str,
) -> None:
    json_path = Path(report_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")

    html_path = Path(dashboard_path)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render_crypto_execution_readiness_dashboard(report), encoding="utf-8")
