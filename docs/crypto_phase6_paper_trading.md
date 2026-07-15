# Crypto Phase 6 Paper Trading

Phase 6 replays Phase 4 forward signals and Phase 5 outcome labels into a virtual account. It answers a different question than Phase 5: not only "is the signal edge positive?", but "what would the account curve look like under simple risk rules?"

This is still not live exchange execution. It does not use exchange API keys, balances, orders, or order management.

## Inputs

- Forward journal: `logs/crypto_forward_journal.jsonl`
- Forward outcomes: `logs/crypto_forward_outcomes.jsonl`

Run Phase 5 first so outcomes exist:

```bash
python -m research.crypto_forward_validation_report --sent-only
```

## Main Command

```bash
python -m research.crypto_paper_trading_report --sent-only
```

Outputs:

- `reports/crypto_paper_trading_report.json`
- `reports/crypto_paper_trading_ledger.csv`
- `reports/crypto_paper_trading_dashboard.html`

## Risk Modes

Fixed dollar risk:

```bash
python -m research.crypto_paper_trading_report \
  --starting-balance 1000 \
  --risk-mode fixed \
  --risk-per-trade 25 \
  --sent-only
```

Equity percentage risk:

```bash
python -m research.crypto_paper_trading_report \
  --starting-balance 1000 \
  --risk-mode equity_pct \
  --risk-pct 0.01 \
  --sent-only
```

## Portfolio Constraints

Default behavior is conservative:

- maximum open paper positions: `1`
- one open position per symbol
- only closed outcomes with a numeric `r_multiple` become executed paper trades
- open, pending, ambiguous-without-R, or missing outcomes are skipped

Allow more simultaneous paper positions:

```bash
python -m research.crypto_paper_trading_report --max-open-positions 2 --sent-only
```

Allow overlapping same-symbol paper positions:

```bash
python -m research.crypto_paper_trading_report --allow-same-symbol-overlap --sent-only
```

## Reading The Report

The JSON report contains:

- account settings
- overall account metrics
- by-symbol and by-regime breakdowns
- full paper ledger
- equity curve events

The CSV ledger is the audit trail. Every candidate is marked as either `executed=true` or skipped with a `skip_reason`.

## Phase 6 Exit Criteria

Before any execution-design phase:

- Phase 4 signal loop runs reliably
- Phase 5 labels enough closed outcomes
- Phase 6 paper account has enough executed trades to evaluate drawdown and ROI
- BTCUSDT and ETHUSDT are reviewed separately
- No exchange keys or live order code are introduced
