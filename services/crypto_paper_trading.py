from __future__ import annotations

import csv
import html
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

import pandas as pd

from services.forward_outcomes import ForwardCandidate, load_candidates, load_latest_outcomes


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: object | None) -> pd.Timestamp | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        timestamp = pd.Timestamp(text)
    except (TypeError, ValueError):
        return None
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _iso(value: pd.Timestamp | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.tz_localize("UTC")
    return value.tz_convert("UTC").isoformat()


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


def _money(value: float) -> float:
    return round(float(value), 2)


def _profit_factor(values: Iterable[float]) -> float | str:
    r_values = [float(value) for value in values]
    gross_win = sum(value for value in r_values if value > 0)
    gross_loss = abs(sum(value for value in r_values if value < 0))
    if gross_loss > 0:
        return round(gross_win / gross_loss, 6)
    if gross_win > 0:
        return "inf"
    return 0.0


@dataclass(frozen=True)
class PaperTradingSettings:
    journal_path: Path | str = Path("logs/crypto_forward_journal.jsonl")
    outcome_path: Path | str = Path("logs/crypto_forward_outcomes.jsonl")
    report_path: Path | str = Path("reports/crypto_paper_trading_report.json")
    ledger_path: Path | str = Path("reports/crypto_paper_trading_ledger.csv")
    dashboard_path: Path | str = Path("reports/crypto_paper_trading_dashboard.html")
    sent_only: bool = True
    starting_balance: float = 1000.0
    risk_mode: str = "fixed"
    risk_per_trade: float = 25.0
    risk_pct: float = 0.01
    account_currency: str = "USD"
    max_open_positions: int = 1
    one_position_per_symbol: bool = True
    min_score: int = 0

    def normalized(self) -> "PaperTradingSettings":
        risk_mode = str(self.risk_mode or "fixed").strip().lower()
        if risk_mode not in {"fixed", "equity_pct"}:
            risk_mode = "fixed"
        return PaperTradingSettings(
            journal_path=Path(self.journal_path),
            outcome_path=Path(self.outcome_path),
            report_path=Path(self.report_path),
            ledger_path=Path(self.ledger_path),
            dashboard_path=Path(self.dashboard_path),
            sent_only=bool(self.sent_only),
            starting_balance=max(0.0, float(self.starting_balance)),
            risk_mode=risk_mode,
            risk_per_trade=max(0.0, float(self.risk_per_trade)),
            risk_pct=max(0.0, min(1.0, float(self.risk_pct))),
            account_currency=str(self.account_currency or "USD").strip().upper() or "USD",
            max_open_positions=max(0, int(self.max_open_positions)),
            one_position_per_symbol=bool(self.one_position_per_symbol),
            min_score=max(0, min(100, int(self.min_score))),
        )


@dataclass
class _PendingTrade:
    row: dict[str, object]
    exit_time: pd.Timestamp
    pnl: float
    r_multiple: float


class CryptoPaperTradingSimulator:
    def __init__(self, settings: PaperTradingSettings) -> None:
        self.settings = settings.normalized()

    def run(self) -> dict[str, object]:
        candidates = load_candidates(self.settings.journal_path, sent_only=self.settings.sent_only)
        outcomes = load_latest_outcomes(self.settings.outcome_path)
        rows = self._simulate(candidates, outcomes)
        return self._report(rows)

    def _simulate(
        self,
        candidates: Iterable[ForwardCandidate],
        outcomes: Mapping[str, Mapping[str, object]],
    ) -> list[dict[str, object]]:
        balance = float(self.settings.starting_balance)
        peak_balance = balance
        min_balance = balance
        max_drawdown = 0.0
        active: list[_PendingTrade] = []
        rows: list[dict[str, object]] = []
        candidates = sorted(candidates, key=lambda item: (item.generated_at, item.symbol, item.journal_id))
        start_time = _iso(candidates[0].generated_at) if candidates else utc_now()
        equity_curve = [
            {
                "time": start_time,
                "balance": _money(balance),
                "event": "start",
            }
        ]

        def close_due_trades(now: pd.Timestamp) -> None:
            nonlocal balance, peak_balance, min_balance, max_drawdown
            due = [trade for trade in active if trade.exit_time <= now]
            for trade in sorted(due, key=lambda item: item.exit_time):
                balance += trade.pnl
                peak_balance = max(peak_balance, balance)
                min_balance = min(min_balance, balance)
                max_drawdown = max(max_drawdown, peak_balance - balance)
                trade.row["balance_after"] = _money(balance)
                trade.row["closed_at"] = _iso(trade.exit_time)
                equity_curve.append(
                    {
                        "time": _iso(trade.exit_time),
                        "balance": _money(balance),
                        "event": "close",
                        "journal_id": trade.row.get("journal_id"),
                        "symbol": trade.row.get("symbol"),
                        "pnl": _money(trade.pnl),
                        "r_multiple": round(float(trade.r_multiple), 6),
                    }
                )
            active[:] = [trade for trade in active if trade.exit_time > now]

        for candidate in candidates:
            outcome = outcomes.get(candidate.journal_id)
            entry_time = _parse_time(outcome.get("entry_time") if outcome else None) or candidate.generated_at
            close_due_trades(entry_time)

            row = self._base_row(candidate, outcome, balance_before=balance)
            skip_reason = self._skip_reason(candidate, outcome)
            if skip_reason is None and self.settings.max_open_positions > 0 and len(active) >= self.settings.max_open_positions:
                skip_reason = "max_open_positions"
            if (
                skip_reason is None
                and self.settings.one_position_per_symbol
                and any(str(trade.row.get("symbol")) == candidate.symbol for trade in active)
            ):
                skip_reason = "symbol_already_open"

            if skip_reason is not None:
                row.update({"executed": False, "skip_reason": skip_reason, "balance_after": _money(balance)})
                rows.append(row)
                continue

            r_multiple = _as_float(outcome.get("r_multiple") if outcome else None)
            risk = self._risk_amount(balance)
            pnl = risk * r_multiple
            exit_time = _parse_time(outcome.get("exit_time") if outcome else None) or entry_time
            row.update(
                {
                    "executed": True,
                    "skip_reason": "",
                    "risk_usd": _money(risk),
                    "pnl_usd": _money(pnl),
                    "r_multiple": round(r_multiple, 6),
                    "entry_time": _iso(entry_time),
                    "exit_time": _iso(exit_time),
                }
            )
            rows.append(row)
            active.append(_PendingTrade(row=row, exit_time=exit_time, pnl=pnl, r_multiple=r_multiple))

        far_future = pd.Timestamp.max.tz_localize("UTC")
        close_due_trades(far_future)
        rows.sort(key=lambda row: (str(row.get("entry_time") or row.get("generated_at") or ""), str(row.get("journal_id"))))
        for row in rows:
            if row.get("balance_after") is None:
                row["balance_after"] = _money(balance)
        self._last_equity_curve = equity_curve
        return rows

    def _risk_amount(self, balance: float) -> float:
        if self.settings.risk_mode == "equity_pct":
            return max(0.0, balance * self.settings.risk_pct)
        return self.settings.risk_per_trade

    def _skip_reason(self, candidate: ForwardCandidate, outcome: Mapping[str, object] | None) -> str | None:
        if candidate.score < self.settings.min_score:
            return "score_below_paper_minimum"
        if outcome is None:
            return "no_outcome"
        if str(outcome.get("status")) != "closed":
            return f"outcome_{outcome.get('status', 'unknown')}"
        if outcome.get("r_multiple") is None:
            return "outcome_without_r"
        return None

    def _base_row(
        self,
        candidate: ForwardCandidate,
        outcome: Mapping[str, object] | None,
        *,
        balance_before: float,
    ) -> dict[str, object]:
        signal = candidate.candidate_event.get("signal")
        signal_context = signal if isinstance(signal, dict) else {}
        return {
            "journal_id": candidate.journal_id,
            "fingerprint": candidate.fingerprint,
            "cycle_id": candidate.cycle_id,
            "symbol": candidate.symbol,
            "side": candidate.side,
            "generated_at": _iso(candidate.generated_at),
            "entry_time": outcome.get("entry_time") if outcome else None,
            "exit_time": outcome.get("exit_time") if outcome else None,
            "score": candidate.score,
            "regime_label": signal_context.get("regime_label"),
            "trigger_event": signal_context.get("trigger_event"),
            "zone": signal_context.get("zone"),
            "entry_mode": candidate.entry_mode,
            "entry_source": candidate.entry_source,
            "outcome_status": outcome.get("status") if outcome else "no_outcome",
            "exit_reason": outcome.get("exit_reason") if outcome else "no_outcome",
            "r_multiple": outcome.get("r_multiple") if outcome else None,
            "risk_usd": 0.0,
            "pnl_usd": 0.0,
            "balance_before": _money(balance_before),
            "balance_after": None,
            "account_currency": self.settings.account_currency,
            "executed": False,
            "skip_reason": "",
        }

    def _report(self, rows: list[dict[str, object]]) -> dict[str, object]:
        executed = [row for row in rows if row.get("executed") is True]
        r_values = [_as_float(row.get("r_multiple")) for row in executed]
        pnl_values = [_as_float(row.get("pnl_usd")) for row in executed]
        wins = [value for value in r_values if value > 0]
        losses = [value for value in r_values if value < 0]
        equity_curve = getattr(self, "_last_equity_curve", [])
        ending_balance = (
            _as_float(equity_curve[-1].get("balance"), self.settings.starting_balance)
            if equity_curve and isinstance(equity_curve[-1], dict)
            else self.settings.starting_balance
        )
        balances = [_as_float(item.get("balance"), self.settings.starting_balance) for item in equity_curve if isinstance(item, dict)]
        min_balance = min(balances) if balances else self.settings.starting_balance
        peak = self.settings.starting_balance
        max_drawdown = 0.0
        for balance in balances:
            peak = max(peak, balance)
            max_drawdown = max(max_drawdown, peak - balance)

        return {
            "type": "crypto_paper_trading_report",
            "version": 1,
            "generated_at": utc_now(),
            "settings": {
                "journal_path": str(self.settings.journal_path),
                "outcome_path": str(self.settings.outcome_path),
                "sent_only": self.settings.sent_only,
                "starting_balance": _money(self.settings.starting_balance),
                "risk_mode": self.settings.risk_mode,
                "risk_per_trade": _money(self.settings.risk_per_trade),
                "risk_pct": self.settings.risk_pct,
                "account_currency": self.settings.account_currency,
                "max_open_positions": self.settings.max_open_positions,
                "one_position_per_symbol": self.settings.one_position_per_symbol,
                "min_score": self.settings.min_score,
            },
            "overall": {
                "candidates": len(rows),
                "executed_trades": len(executed),
                "skipped": len(rows) - len(executed),
                "wins": len(wins),
                "losses": len(losses),
                "breakeven": sum(1 for value in r_values if value == 0),
                "win_rate": round(len(wins) / len(r_values), 6) if r_values else 0.0,
                "avg_r": round(sum(r_values) / len(r_values), 6) if r_values else 0.0,
                "total_r": round(sum(r_values), 6) if r_values else 0.0,
                "profit_factor": _profit_factor(r_values),
                "starting_balance": _money(self.settings.starting_balance),
                "final_balance": _money(ending_balance),
                "min_balance": _money(min_balance),
                "net_pnl": _money(sum(pnl_values)),
                "gross_profit": _money(sum(value for value in pnl_values if value > 0)),
                "gross_loss": _money(abs(sum(value for value in pnl_values if value < 0))),
                "roi_pct": round(
                    ((ending_balance - self.settings.starting_balance) / self.settings.starting_balance * 100.0),
                    4,
                )
                if self.settings.starting_balance > 0
                else 0.0,
                "max_drawdown": _money(max_drawdown),
                "max_drawdown_pct": round((max_drawdown / peak * 100.0), 4) if peak > 0 else 0.0,
                "skip_reasons": dict(Counter(str(row.get("skip_reason") or "executed") for row in rows)),
            },
            "by_symbol": self._group(executed, "symbol"),
            "by_regime": self._group(executed, "regime_label"),
            "ledger": rows,
            "equity_curve": equity_curve,
        }

    def _group(self, rows: Iterable[Mapping[str, object]], key: str) -> dict[str, dict[str, object]]:
        grouped: defaultdict[str, list[Mapping[str, object]]] = defaultdict(list)
        for row in rows:
            grouped[str(row.get(key) or "UNKNOWN")].append(row)

        output: dict[str, dict[str, object]] = {}
        for group_key, group_rows in sorted(grouped.items()):
            r_values = [_as_float(row.get("r_multiple")) for row in group_rows]
            pnl_values = [_as_float(row.get("pnl_usd")) for row in group_rows]
            wins = [value for value in r_values if value > 0]
            output[group_key] = {
                "trades": len(group_rows),
                "win_rate": round(len(wins) / len(r_values), 6) if r_values else 0.0,
                "avg_r": round(sum(r_values) / len(r_values), 6) if r_values else 0.0,
                "total_r": round(sum(r_values), 6) if r_values else 0.0,
                "net_pnl": _money(sum(pnl_values)),
                "profit_factor": _profit_factor(r_values),
            }
        return output


def write_paper_trading_outputs(report: Mapping[str, object], settings: PaperTradingSettings) -> None:
    cfg = settings.normalized()
    report_path = Path(cfg.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")

    ledger_rows = report.get("ledger") if isinstance(report.get("ledger"), list) else []
    ledger_path = Path(cfg.ledger_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "journal_id",
        "symbol",
        "side",
        "generated_at",
        "entry_time",
        "exit_time",
        "score",
        "regime_label",
        "outcome_status",
        "exit_reason",
        "executed",
        "skip_reason",
        "r_multiple",
        "risk_usd",
        "pnl_usd",
        "balance_before",
        "balance_after",
        "account_currency",
    ]
    with ledger_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in ledger_rows:
            writer.writerow(row if isinstance(row, dict) else {})

    dashboard_path = Path(cfg.dashboard_path)
    dashboard_path.parent.mkdir(parents=True, exist_ok=True)
    dashboard_path.write_text(render_paper_trading_dashboard(report), encoding="utf-8")


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _fmt(value: object, digits: int = 2) -> str:
    numeric = _as_float(value)
    if numeric == float("inf"):
        return "inf"
    return f"{numeric:.{digits}f}"


def _metric(label: str, value: object, detail: str = "") -> str:
    detail_html = f"<span>{_escape(detail)}</span>" if detail else ""
    return f'<div class="metric"><div>{_escape(label)}</div><strong>{_escape(value)}</strong>{detail_html}</div>'


def _group_table(title: str, groups: Mapping[str, object]) -> str:
    rows = []
    for key, value in groups.items():
        row = value if isinstance(value, dict) else {}
        rows.append(
            "<tr>"
            f"<td>{_escape(key)}</td>"
            f"<td>{_as_int(row.get('trades'))}</td>"
            f"<td>{_fmt(_as_float(row.get('win_rate')) * 100, 1)}%</td>"
            f"<td>{_fmt(row.get('avg_r'), 3)}</td>"
            f"<td>{_escape(row.get('profit_factor', 0.0))}</td>"
            f"<td>{_fmt(row.get('net_pnl'), 2)}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="6" class="empty">No executed paper trades yet</td></tr>')
    return (
        '<section class="panel">'
        f"<h2>{_escape(title)}</h2>"
        "<table><thead><tr><th>Name</th><th>Trades</th><th>Win</th><th>AvgR</th><th>PF</th><th>Net</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
    )


def render_paper_trading_dashboard(report: Mapping[str, object]) -> str:
    overall = report.get("overall") if isinstance(report.get("overall"), dict) else {}
    by_symbol = report.get("by_symbol") if isinstance(report.get("by_symbol"), dict) else {}
    by_regime = report.get("by_regime") if isinstance(report.get("by_regime"), dict) else {}
    settings = report.get("settings") if isinstance(report.get("settings"), dict) else {}
    skip_reasons = overall.get("skip_reasons") if isinstance(overall.get("skip_reasons"), dict) else {}
    skip_rows = "".join(
        f"<tr><td>{_escape(key)}</td><td>{_as_int(value)}</td></tr>"
        for key, value in sorted(skip_reasons.items())
    ) or '<tr><td colspan="2" class="empty">No decisions yet</td></tr>'

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Crypto Paper Trading Dashboard</title>
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
    header {{ display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 16px; }}
    h1, h2 {{ margin: 0; font-weight: 600; letter-spacing: 0; }}
    h1 {{ font-size: 24px; }}
    h2 {{ font-size: 16px; margin-bottom: 10px; }}
    .subtle {{ color: var(--muted); font-size: 13px; margin-top: 4px; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 14px; }}
    .metric, .panel {{ border: 1px solid var(--border); border-radius: 8px; background: var(--panel); }}
    .metric {{ padding: 12px; min-height: 86px; }}
    .metric div {{ color: var(--muted); font-size: 13px; }}
    .metric strong {{ display: block; font-size: 24px; margin-top: 4px; }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; margin-top: 4px; }}
    .panels {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
    .panel {{ padding: 14px; overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ text-align: left; border-bottom: 1px solid var(--border); padding: 8px 6px; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 600; }}
    tr:last-child td {{ border-bottom: 0; }}
    .empty {{ color: var(--muted); text-align: center; }}
    @media (max-width: 760px) {{ main {{ padding: 16px; }} header {{ display: block; }} .grid, .panels {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Crypto Paper Trading</h1>
        <div class="subtle">Generated {_escape(report.get("generated_at", ""))}</div>
      </div>
      <div class="subtle">Risk: {_escape(settings.get("risk_mode", ""))} | {_escape(settings.get("account_currency", "USD"))}</div>
    </header>
    <section class="grid">
      {_metric("Final Balance", _fmt(overall.get("final_balance"), 2), f"start {_fmt(overall.get('starting_balance'), 2)}")}
      {_metric("Net PnL", _fmt(overall.get("net_pnl"), 2), f"ROI {_fmt(overall.get('roi_pct'), 2)}%")}
      {_metric("Executed Trades", _as_int(overall.get("executed_trades")), f"skipped {_as_int(overall.get('skipped'))}")}
      {_metric("Max Drawdown", _fmt(overall.get("max_drawdown"), 2), f"{_fmt(overall.get('max_drawdown_pct'), 2)}%")}
    </section>
    <section class="panels">
      {_group_table("By Symbol", by_symbol)}
      {_group_table("By Regime", by_regime)}
      <section class="panel">
        <h2>Decision Counts</h2>
        <table><thead><tr><th>Reason</th><th>Count</th></tr></thead><tbody>{skip_rows}</tbody></table>
      </section>
      <section class="panel">
        <h2>Account</h2>
        <table><tbody>
          <tr><th>Win rate</th><td>{_fmt(_as_float(overall.get("win_rate")) * 100, 1)}%</td></tr>
          <tr><th>Avg R</th><td>{_fmt(overall.get("avg_r"), 3)}</td></tr>
          <tr><th>Total R</th><td>{_fmt(overall.get("total_r"), 3)}</td></tr>
          <tr><th>Profit factor</th><td>{_escape(overall.get("profit_factor", 0.0))}</td></tr>
        </tbody></table>
      </section>
    </section>
  </main>
</body>
</html>
"""
