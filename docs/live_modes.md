# Live Modes

Live modes are optional overlays for the Telegram signal engine. They do not change backtest logic and remain disabled unless `ENABLE_LIVE_MODE=1`.

For crypto Phase 4, prefer `python -m research.crypto_forward_validation` and `docs/phase4_crypto_forward_validation.env.example`. That workflow uses explicit BTCUSDT/ETHUSDT pair profiles instead of these preset live-mode overlays.

## Disabled / Legacy

```env
ENABLE_LIVE_MODE=0
```

The engine uses normal `.env` settings.

## Balanced

Crypto profile:

```text
Pairs: BTCUSDT, ETHUSDT
Session: 24/7
Min score: 80
Blocked regime: none
Exit profile: m15_vol_liq_v1
```

Enable:

```env
ENABLE_LIVE_MODE=1
LIVE_MODE=balanced
```

## Aggressive

Crypto profile:

```text
Pairs: BTCUSDT, ETHUSDT, SOLUSDT
Session: 24/7
Min score: 78
Blocked regime: none
Exit profile: m15_vol_liq_v1
```

Enable:

```env
ENABLE_LIVE_MODE=1
LIVE_MODE=aggressive
```

Notes:

- The aggressive preset is not the Phase 4 recommended profile.
- Keep Phase 4 on BTCUSDT/ETHUSDT until forward evidence supports adding SOLUSDT.

## Conservative

Crypto profile:

```text
Pairs: BTCUSDT
Session: 24/7
Min score: 80
Blocked regime: none
Exit profile: m15_vol_liq_v1
```

Enable:

```env
ENABLE_LIVE_MODE=1
LIVE_MODE=conservative
```

## Notes

- Live modes only send Telegram signals; they do not auto-trade.
- `balanced` is designed for higher signal frequency.
- `aggressive` is experimental and should be used only when more signal flow is worth lower quality.
- `conservative` is designed for lower noise and prop-style caution.
- If `LIVE_MODE` is invalid, the engine falls back to legacy settings.
- Use `docs/pair_profiles.md` when each pair needs its own custom threshold/session/regime rules.
