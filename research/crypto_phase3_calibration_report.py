from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize Phase 3 crypto backtest exports.")
    parser.add_argument("report_dirs", nargs="+", help="Backtest export directories to summarize")
    parser.add_argument("--output-json", default=None, help="Optional JSON summary output path")
    return parser


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    return payload if isinstance(payload, dict) else {}


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def summarize_report(path: Path) -> dict[str, Any]:
    summary = read_json(path / "summary.json")
    pair_rows = read_csv_rows(path / "pair_summary.csv")
    trades = read_csv_rows(path / "trades.csv")
    if isinstance(summary.get("overall"), dict):
        overall = summary["overall"]
    elif isinstance(summary.get("overall_metrics"), dict):
        overall = summary["overall_metrics"]
    else:
        overall = {}
    parameters = summary.get("parameters") if isinstance(summary.get("parameters"), dict) else {}

    by_regime: dict[str, dict[str, float]] = {}
    for trade in trades:
        regime = str(trade.get("regime_label") or "UNKNOWN").upper()
        row = by_regime.setdefault(regime, {"trades": 0.0, "total_r": 0.0, "wins": 0.0, "losses": 0.0})
        r_multiple = as_float(trade.get("r_multiple"))
        row["trades"] += 1
        row["total_r"] += r_multiple
        if r_multiple > 0:
            row["wins"] += 1
        elif r_multiple < 0:
            row["losses"] += 1
    for row in by_regime.values():
        trades_count = row["trades"]
        row["avg_r"] = round(row["total_r"] / trades_count, 4) if trades_count else 0.0
        row["win_rate"] = round(row["wins"] / trades_count, 4) if trades_count else 0.0

    return {
        "name": path.name,
        "path": str(path),
        "parameters": {
            "history_limit": parameters.get("history_limit"),
            "evaluation_step": parameters.get("evaluation_step"),
            "pair_evaluation_steps": parameters.get("pair_evaluation_steps"),
            "enable_realistic_execution": parameters.get("enable_realistic_execution"),
            "spread_by_pair": parameters.get("spread_by_pair"),
            "slippage_mode": parameters.get("slippage_mode"),
            "max_slippage_pips": parameters.get("max_slippage_pips"),
        },
        "overall": {
            "trades": int(overall.get("trades", 0) or 0),
            "win_rate": round(as_float(overall.get("win_rate")), 4),
            "avg_r": round(as_float(overall.get("avg_r")), 4),
            "profit_factor": overall.get("profit_factor", 0),
            "max_drawdown_r": round(as_float(overall.get("max_drawdown_r")), 4),
            "roi_pct": round(as_float(overall.get("roi_pct")), 4),
        },
        "pairs": [
            {
                "pair": row.get("pair"),
                "trades": int(as_float(row.get("trades"))),
                "win_rate": round(as_float(row.get("win_rate")), 4),
                "avg_r": round(as_float(row.get("avg_r")), 4),
                "max_drawdown_r": round(as_float(row.get("max_drawdown_r")), 4),
                "acceptance_rate": round(as_float(row.get("acceptance_rate")), 4),
                "rejections": row.get("rejections"),
            }
            for row in pair_rows
        ],
        "regimes": by_regime,
    }


def print_summary(rows: list[dict[str, Any]]) -> None:
    print("name,trades,win_rate,avg_r,max_dd_r,roi_pct,realistic")
    for row in rows:
        overall = row["overall"]
        params = row["parameters"]
        print(
            "{name},{trades},{win_rate:.4f},{avg_r:.4f},{dd:.4f},{roi:.4f},{realistic}".format(
                name=row["name"],
                trades=overall["trades"],
                win_rate=overall["win_rate"],
                avg_r=overall["avg_r"],
                dd=overall["max_drawdown_r"],
                roi=overall["roi_pct"],
                realistic=bool(params.get("enable_realistic_execution")),
            )
        )


def main() -> None:
    args = build_parser().parse_args()
    rows = [summarize_report(Path(item)) for item in args.report_dirs]
    print_summary(rows)
    if args.output_json:
        output = Path(args.output_json)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"reports": rows}, indent=2, sort_keys=True), encoding="utf-8")
        print(f"wrote {output}")


if __name__ == "__main__":
    main()
