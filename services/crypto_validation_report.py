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


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


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


def _fmt_pct(value: object) -> str:
    return f"{_as_float(value) * 100:.1f}%"


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


@dataclass(frozen=True)
class CryptoValidationThresholds:
    min_closed_trades: int = 30
    min_avg_r: float = 0.10
    min_profit_factor: float = 1.20
    max_drawdown_r: float = 3.0

    def normalized(self) -> "CryptoValidationThresholds":
        return CryptoValidationThresholds(
            min_closed_trades=max(1, int(self.min_closed_trades)),
            min_avg_r=float(self.min_avg_r),
            min_profit_factor=max(0.0, float(self.min_profit_factor)),
            max_drawdown_r=max(0.0, float(self.max_drawdown_r)),
        )


def _top_groups(
    groups: Mapping[str, object],
    *,
    reverse: bool,
    limit: int = 5,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for key, value in groups.items():
        row = _as_dict(value)
        closed = _as_int(row.get("closed_with_r"))
        if closed <= 0:
            continue
        rows.append(
            {
                "name": str(key),
                "candidates": _as_int(row.get("candidates")),
                "closed_with_r": closed,
                "win_rate": _as_float(row.get("win_rate")),
                "avg_r": _as_float(row.get("avg_r")),
                "profit_factor": row.get("profit_factor", 0.0),
                "total_r": _as_float(row.get("total_r")),
                "max_drawdown_r": _as_float(row.get("max_drawdown_r")),
            }
        )
    rows.sort(
        key=lambda row: (
            _as_float(row.get("avg_r")),
            _as_float(row.get("total_r")),
            _as_int(row.get("closed_with_r")),
        ),
        reverse=reverse,
    )
    return rows[:limit]


def _watchlist(groups: Mapping[str, object], *, min_closed: int = 3) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for key, value in groups.items():
        row = _as_dict(value)
        closed = _as_int(row.get("closed_with_r"))
        avg_r = _as_float(row.get("avg_r"))
        pf = _as_float(row.get("profit_factor"))
        if closed >= min_closed and (avg_r < 0.0 or pf < 1.0):
            rows.append(
                {
                    "name": str(key),
                    "closed_with_r": closed,
                    "avg_r": avg_r,
                    "profit_factor": row.get("profit_factor", 0.0),
                    "max_drawdown_r": _as_float(row.get("max_drawdown_r")),
                }
            )
    rows.sort(key=lambda row: (_as_float(row["avg_r"]), -_as_int(row["closed_with_r"])))
    return rows


def _recommendation(
    overall: Mapping[str, object],
    health: Mapping[str, object],
    thresholds: CryptoValidationThresholds,
) -> dict[str, object]:
    closed = _as_int(overall.get("closed_with_r"))
    candidates = _as_int(overall.get("candidates"))
    avg_r = _as_float(overall.get("avg_r"))
    pf = _as_float(overall.get("profit_factor"))
    drawdown = _as_float(overall.get("max_drawdown_r"))

    if health and health.get("ok") is not True:
        return {
            "action": "FIX_FEED_HEALTH",
            "readiness": "not_ready",
            "reason": str(health.get("reason") or "feed health is not OK"),
        }
    if candidates <= 0:
        return {
            "action": "COLLECT_SIGNALS",
            "readiness": "not_ready",
            "reason": "no forward candidates are in the journal yet",
        }
    if closed < thresholds.min_closed_trades:
        return {
            "action": "COLLECT_MORE_FORWARD_DATA",
            "readiness": "collecting",
            "reason": f"closed sample {closed} < minimum {thresholds.min_closed_trades}",
        }
    if avg_r < 0.0 or pf < 1.0:
        return {
            "action": "PAUSE_OR_TIGHTEN_PROFILE",
            "readiness": "not_ready",
            "reason": "forward expectancy is negative or profit factor is below 1.0",
        }
    if drawdown > thresholds.max_drawdown_r:
        return {
            "action": "REVIEW_DRAWDOWN",
            "readiness": "needs_review",
            "reason": f"max drawdown {drawdown:.2f}R > limit {thresholds.max_drawdown_r:.2f}R",
        }
    if avg_r >= thresholds.min_avg_r and pf >= thresholds.min_profit_factor:
        return {
            "action": "KEEP_PROFILE_SIGNAL_ONLY",
            "readiness": "validation_pass",
            "reason": "forward expectancy and profit factor meet validation thresholds",
        }
    return {
        "action": "KEEP_COLLECTING_REVIEW_GROUPS",
        "readiness": "needs_review",
        "reason": "sample is large enough, but expectancy thresholds are not fully met",
    }


def build_crypto_validation_report(
    *,
    performance_report: Mapping[str, object],
    outcome_update: Mapping[str, object] | None = None,
    health: Mapping[str, object] | None = None,
    thresholds: CryptoValidationThresholds | None = None,
    generated_paths: Mapping[str, object] | None = None,
) -> dict[str, object]:
    threshold_values = (thresholds or CryptoValidationThresholds()).normalized()
    overall = _as_dict(performance_report.get("overall"))
    by_pair = _as_dict(performance_report.get("by_pair"))
    by_regime = _as_dict(performance_report.get("by_regime"))
    by_session = _as_dict(performance_report.get("by_session"))
    by_score = _as_dict(performance_report.get("by_score_bucket"))
    rows = _as_list(performance_report.get("rows"))

    health_payload = _as_dict(health)
    recommendation = _recommendation(overall, health_payload, threshold_values)
    report = {
        "type": "crypto_forward_validation_report",
        "version": 1,
        "generated_at": utc_now(),
        "thresholds": {
            "min_closed_trades": threshold_values.min_closed_trades,
            "min_avg_r": threshold_values.min_avg_r,
            "min_profit_factor": threshold_values.min_profit_factor,
            "max_drawdown_r": threshold_values.max_drawdown_r,
        },
        "recommendation": recommendation,
        "overall": overall,
        "leaders": {
            "best_pairs": _top_groups(by_pair, reverse=True),
            "worst_pairs": _top_groups(by_pair, reverse=False),
            "best_regimes": _top_groups(by_regime, reverse=True),
            "worst_regimes": _top_groups(by_regime, reverse=False),
            "best_sessions": _top_groups(by_session, reverse=True),
            "worst_sessions": _top_groups(by_session, reverse=False),
        },
        "watchlist": {
            "pairs": _watchlist(by_pair),
            "regimes": _watchlist(by_regime),
            "sessions": _watchlist(by_session),
            "score_buckets": _watchlist(by_score),
        },
        "groups": {
            "by_pair": by_pair,
            "by_regime": by_regime,
            "by_session": by_session,
            "by_score_bucket": by_score,
        },
        "recent_rows": rows[-25:],
        "outcome_update": dict(outcome_update or {}),
        "health": health_payload,
        "paths": dict(generated_paths or {}),
    }
    return report


def _metric(label: str, value: object, detail: str = "") -> str:
    detail_html = f"<span>{_escape(detail)}</span>" if detail else ""
    return (
        '<div class="metric">'
        f"<div>{_escape(label)}</div>"
        f"<strong>{_escape(value)}</strong>"
        f"{detail_html}"
        "</div>"
    )


def _group_table(title: str, rows: Sequence[Mapping[str, object]]) -> str:
    body = []
    for row in rows:
        body.append(
            "<tr>"
            f"<td>{_escape(row.get('name', ''))}</td>"
            f"<td>{_as_int(row.get('closed_with_r'))}</td>"
            f"<td>{_fmt_pct(row.get('win_rate'))}</td>"
            f"<td>{_fmt_number(row.get('avg_r'), 3)}</td>"
            f"<td>{_escape(_fmt_number(row.get('profit_factor'), 2) if not isinstance(row.get('profit_factor'), str) else row.get('profit_factor'))}</td>"
            f"<td>{_fmt_number(row.get('max_drawdown_r'), 2)}</td>"
            "</tr>"
        )
    if not body:
        body.append('<tr><td colspan="6" class="empty">No closed rows yet</td></tr>')
    return (
        '<section class="panel">'
        f"<h2>{_escape(title)}</h2>"
        "<table><thead><tr><th>Name</th><th>Closed</th><th>Win</th><th>AvgR</th><th>PF</th><th>DD</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
        "</section>"
    )


def _watchlist_table(rows: Sequence[Mapping[str, object]]) -> str:
    body = []
    for row in rows:
        body.append(
            "<tr>"
            f"<td>{_escape(row.get('name', ''))}</td>"
            f"<td>{_as_int(row.get('closed_with_r'))}</td>"
            f"<td>{_fmt_number(row.get('avg_r'), 3)}</td>"
            f"<td>{_escape(_fmt_number(row.get('profit_factor'), 2) if not isinstance(row.get('profit_factor'), str) else row.get('profit_factor'))}</td>"
            f"<td>{_fmt_number(row.get('max_drawdown_r'), 2)}</td>"
            "</tr>"
        )
    if not body:
        body.append('<tr><td colspan="5" class="empty">No negative groups with enough sample</td></tr>')
    return (
        '<section class="panel wide">'
        "<h2>Watchlist</h2>"
        "<table><thead><tr><th>Group</th><th>Closed</th><th>AvgR</th><th>PF</th><th>DD</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
        "</section>"
    )


def _recent_table(rows: Sequence[object]) -> str:
    body = []
    for raw in rows:
        row = _as_dict(raw)
        body.append(
            "<tr>"
            f"<td>{_escape(row.get('generated_at', ''))}</td>"
            f"<td>{_escape(row.get('symbol', ''))}</td>"
            f"<td>{_escape(row.get('side', ''))}</td>"
            f"<td>{_as_int(row.get('score'))}</td>"
            f"<td>{_escape(row.get('outcome_status', ''))}</td>"
            f"<td>{_escape(row.get('exit_reason', ''))}</td>"
            f"<td>{_fmt_number(row.get('r_multiple'), 3) if row.get('r_multiple') is not None else '-'}</td>"
            "</tr>"
        )
    if not body:
        body.append('<tr><td colspan="7" class="empty">No recent candidates</td></tr>')
    return (
        '<section class="panel wide">'
        "<h2>Recent Candidates</h2>"
        "<table><thead><tr><th>UTC</th><th>Pair</th><th>Side</th><th>Score</th><th>Status</th><th>Exit</th><th>R</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
        "</section>"
    )


def render_crypto_validation_dashboard(report: Mapping[str, object]) -> str:
    overall = _as_dict(report.get("overall"))
    recommendation = _as_dict(report.get("recommendation"))
    leaders = _as_dict(report.get("leaders"))
    watchlist = _as_dict(report.get("watchlist"))
    health = _as_dict(report.get("health"))
    recent_rows = _as_list(report.get("recent_rows"))
    watch_rows = (
        _as_list(watchlist.get("pairs"))
        + _as_list(watchlist.get("regimes"))
        + _as_list(watchlist.get("sessions"))
        + _as_list(watchlist.get("score_buckets"))
    )

    status_class = "ok" if recommendation.get("readiness") == "validation_pass" else "warn"
    if recommendation.get("readiness") == "not_ready":
        status_class = "bad"
    health_label = "OK" if health.get("ok") is True else ("Missing" if not health else "Review")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Crypto Forward Validation Dashboard</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: Canvas;
      --fg: CanvasText;
      --muted: color-mix(in srgb, CanvasText 62%, Canvas);
      --panel: color-mix(in srgb, Canvas 92%, CanvasText);
      --border: color-mix(in srgb, CanvasText 18%, Canvas);
      --ok: #15803d;
      --warn: #a16207;
      --bad: #b91c1c;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--fg);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.45;
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 24px;
    }}
    header {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 16px;
      align-items: start;
      border-bottom: 1px solid var(--border);
      padding-bottom: 16px;
      margin-bottom: 18px;
    }}
    h1, h2 {{
      margin: 0;
      font-weight: 600;
      letter-spacing: 0;
    }}
    h1 {{ font-size: 24px; }}
    h2 {{ font-size: 16px; margin-bottom: 10px; }}
    .subtle {{ color: var(--muted); font-size: 13px; margin-top: 4px; }}
    .badge {{
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 4px 10px;
      font-size: 13px;
      font-weight: 600;
      white-space: nowrap;
    }}
    .badge.ok {{ color: var(--ok); }}
    .badge.warn {{ color: var(--warn); }}
    .badge.bad {{ color: var(--bad); }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 14px;
    }}
    .metric, .panel {{
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--panel);
    }}
    .metric {{ padding: 12px; min-height: 86px; }}
    .metric div {{ color: var(--muted); font-size: 13px; }}
    .metric strong {{ display: block; font-size: 24px; margin-top: 4px; }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; margin-top: 4px; }}
    .panels {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }}
    .panel {{ padding: 14px; overflow-x: auto; }}
    .panel.wide {{ grid-column: 1 / -1; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ text-align: left; border-bottom: 1px solid var(--border); padding: 8px 6px; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 600; }}
    tr:last-child td {{ border-bottom: 0; }}
    .empty {{ color: var(--muted); text-align: center; }}
    @media (max-width: 840px) {{
      main {{ padding: 16px; }}
      header {{ grid-template-columns: 1fr; }}
      .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .panels {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 520px) {{
      .grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Crypto Forward Validation</h1>
        <div class="subtle">Generated {_escape(report.get("generated_at", ""))}</div>
      </div>
      <div>
        <span class="badge {status_class}">{_escape(recommendation.get("action", "UNKNOWN"))}</span>
        <span class="badge {'ok' if health_label == 'OK' else 'warn'}">Feed {health_label}</span>
      </div>
    </header>
    <section class="grid">
      {_metric("Candidates", _as_int(overall.get("candidates")), f"delivered {_as_int(overall.get('delivered'))}")}
      {_metric("Closed With R", _as_int(overall.get("closed_with_r")), f"minimum {_as_dict(report.get('thresholds')).get('min_closed_trades', '-')}" )}
      {_metric("Avg R", _fmt_number(overall.get("avg_r"), 3), f"total {_fmt_number(overall.get('total_r'), 3)}R")}
      {_metric("Profit Factor", _escape(overall.get("profit_factor", 0.0)), f"max DD {_fmt_number(overall.get('max_drawdown_r'), 2)}R")}
    </section>
    <section class="panel wide">
      <h2>Decision</h2>
      <table><tbody>
        <tr><th>Readiness</th><td>{_escape(recommendation.get("readiness", ""))}</td></tr>
        <tr><th>Reason</th><td>{_escape(recommendation.get("reason", ""))}</td></tr>
      </tbody></table>
    </section>
    <section class="panels">
      {_group_table("Best Pairs", _as_list(leaders.get("best_pairs")))}
      {_group_table("Worst Pairs", _as_list(leaders.get("worst_pairs")))}
      {_group_table("Best Regimes", _as_list(leaders.get("best_regimes")))}
      {_group_table("Worst Regimes", _as_list(leaders.get("worst_regimes")))}
      {_watchlist_table(watch_rows)}
      {_recent_table(recent_rows)}
    </section>
  </main>
</body>
</html>
"""


def write_crypto_validation_outputs(
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
    html_path.write_text(render_crypto_validation_dashboard(report), encoding="utf-8")
