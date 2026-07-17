# Crypto Phase 10 Testnet Order Preview

Phase 10 prepares the first testnet-facing execution artifact without sending orders. It consumes Phase 9 sandbox execution reports and converts accepted dry-run orders into CCXT-style sandbox `create_order` request previews.

It does not import exchange credentials, instantiate a CCXT trading client, call `create_order`, or submit real/testnet orders.

## Main Command

```bash
python -m research.crypto_testnet_order_preview
```

Outputs:

- `reports/crypto_phase10_testnet_order_preview.json`
- `reports/crypto_phase10_testnet_order_requests.csv`

## What It Adds

- CCXT-style sandbox request payloads
- exchange id and market type metadata
- deterministic Phase 10 request ids
- symbol allowlist checks
- spot long-only policy checks
- Phase 9 source-safety checks
- explicit `order_submission_attempted=false`
- explicit `live_execution_allowed=false`

## Safety Gates

Phase 10 blocks request previews when:

- the Phase 9 report is missing or not ready
- Phase 9 reconciliation is not clean
- any Phase 9 source reports `live_order_sent > 0`
- Phase 9 unexpectedly reports `live_execution_allowed=true`
- the order symbol is outside the allowlist
- a spot order is not `BUY` while long-only policy is active
- quantity, order type, or limit price is invalid

## Diagnostic Runs

Preview even when Phase 9 has not reached ready status:

```bash
python -m research.crypto_testnet_order_preview --allow-phase9-not-ready
```

Allow non-BUY spot request previews for design review only:

```bash
python -m research.crypto_testnet_order_preview --allow-non-buy-spot
```

## Runner Script

```bash
scripts/run_crypto_testnet_order_preview.sh
```

Pass options through:

```bash
scripts/run_crypto_testnet_order_preview.sh --allow-phase9-not-ready
```

## Environment

Use `docs/phase10_crypto_testnet_order_preview.env.example` as the explicit profile for Phase 10 paths, exchange metadata, and safety gates.

## Phase 10 Exit Criteria

Before any future testnet submission phase:

- Phase 9 reports are generated from real Phase 8 dry-run intents
- request previews are generated for BTCUSDT and ETHUSDT
- every request has `test=true` and `sandbox=true`
- every report keeps `order_submission_attempted=false`
- every report keeps `live_execution_allowed=false`
- testnet credential loading and exchange-client construction remain absent
