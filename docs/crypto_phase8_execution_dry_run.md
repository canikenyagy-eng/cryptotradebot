# Crypto Phase 8 Execution Dry Run

Phase 8 starts execution design without live execution. It converts eligible Phase 4 forward candidates into dry-run order intents, validates them against exchange-style constraints, and writes preflight reports for review.

It does not use exchange API keys, account balances, order routing, or order placement.

## Main Command

```bash
python -m research.crypto_execution_dry_run --sent-only
```

Outputs:

- `reports/crypto_phase8_execution_dry_run.json`
- `reports/crypto_phase8_order_intents.csv`
- `reports/crypto_phase8_execution_dry_run.html`

## What It Checks

The dry-run preflight validates:

- Phase 7 readiness has passed, unless `--ignore-phase7-not-ready` is used
- signal score meets the configured dry-run minimum
- spot sell-side intents are blocked by default
- entry, stop, and take-profit are rounded to tick size
- quantity respects `quantity_step` and `min_order_size`
- notional is above the minimum and below the maximum
- risk per intent is capped by `PHASE8_MAX_RISK_QUOTE`
- every ticket is marked `dry_run_only=true`
- report-level `live_execution_allowed=false`

## Local Diagnostic Runs

Build dry-run output even if Phase 7 has not passed yet:

```bash
python -m research.crypto_execution_dry_run \
  --include-unsent \
  --ignore-phase7-not-ready
```

Use equity-percentage sizing:

```bash
python -m research.crypto_execution_dry_run \
  --sent-only \
  --risk-mode equity_pct \
  --account-equity 1000 \
  --risk-pct 0.01
```

Allow spot sell intents for design review only:

```bash
python -m research.crypto_execution_dry_run \
  --include-unsent \
  --ignore-phase7-not-ready \
  --allow-spot-sell
```

## Runner Script

```bash
scripts/run_crypto_execution_dry_run.sh
```

Pass options through:

```bash
scripts/run_crypto_execution_dry_run.sh --include-unsent --ignore-phase7-not-ready
```

## Environment

Use `docs/phase8_crypto_execution_dry_run.env.example` as the explicit profile for Phase 8 paths, sizing, and preflight thresholds.

## Phase 8 Exit Criteria

Before considering any live-execution implementation:

- Phase 7 readiness passes without overrides
- Phase 8 dry-run tickets pass for live-delivered BTCUSDT and ETHUSDT candidates
- sell-side spot behavior is explicitly decided: block, margin-only, or futures-only
- exchange error handling, retry policy, idempotency keys, kill switch, and manual disable flow are documented
- fees, funding, slippage, minimum notional, tick size, and quantity step are reviewed against the target exchange
- `live_execution_allowed` remains `false` in Phase 8 reports
