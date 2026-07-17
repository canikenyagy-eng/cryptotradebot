# Crypto Phase 9 Sandbox Execution

Phase 9 adds the execution architecture skeleton without real exchange execution. It consumes Phase 8 dry-run order intents, runs them through a dry-run or sandbox-stub adapter, records lifecycle states, and writes a reconciliation report.

It does not use exchange API keys, balances, live order routes, or real-money order placement.

## Main Command

```bash
python -m research.crypto_sandbox_execution
```

Outputs:

- `reports/crypto_phase9_sandbox_execution_report.json`
- `reports/crypto_phase9_sandbox_order_ledger.csv`

## What It Adds

- exchange adapter interface
- dry-run execution adapter
- sandbox/testnet adapter stub
- deterministic idempotency keys as `client_order_id`
- order lifecycle states
- kill-switch checks before adapter submission
- reconciliation summary
- explicit `live_execution_allowed=false`
- tests that fail if an adapter reports `live_order_sent=true`

## Modes

Default dry-run mode:

```bash
python -m research.crypto_sandbox_execution --mode dry_run
```

Sandbox/testnet stub mode, still simulated only:

```bash
python -m research.crypto_sandbox_execution \
  --mode sandbox_stub \
  --allow-sandbox-stub
```

If `--allow-sandbox-stub` is omitted, the sandbox stub rejects selected intents. This keeps the future testnet adapter path explicit.

## Kill Switch

Default kill-switch path:

```text
logs/crypto_execution_kill_switch.json
```

Create this file to block Phase 9 immediately:

```json
{"enabled": true, "reason": "manual halt"}
```

Delete it, leave it empty, or set `enabled=false` to allow simulation again.

## Runner Script

```bash
scripts/run_crypto_sandbox_execution.sh
```

Pass options through:

```bash
scripts/run_crypto_sandbox_execution.sh --mode sandbox_stub --allow-sandbox-stub
```

## Environment

Use `docs/phase9_crypto_sandbox_execution.env.example` as the explicit profile for Phase 9 paths and safety switches.

## Phase 9 Exit Criteria

Before considering any real exchange integration:

- Phase 8 dry-run intents exist from real delivered signals
- dry-run mode reconciles with `live_order_sent=0`
- sandbox-stub mode can simulate only when explicitly enabled
- kill switch blocks selected intents
- duplicate idempotency keys are detected
- real exchange order functions are still absent
- `live_execution_allowed` remains `false` in every Phase 9 report
