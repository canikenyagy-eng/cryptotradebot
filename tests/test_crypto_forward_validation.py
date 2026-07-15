from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from config import Settings
from research.crypto_forward_validation import apply_phase4_defaults, phase4_default_env
from services.feed_health import build_feed_health_components


class CryptoForwardValidationTests(unittest.TestCase):
    def test_phase4_defaults_enable_signal_only_crypto_stack(self) -> None:
        defaults = phase4_default_env()
        self.assertEqual(defaults["MARKET_TYPE"], "crypto_spot")
        self.assertEqual(defaults["DATA_SOURCE"], "ccxt")
        self.assertEqual(defaults["PAIRS"], "BTCUSDT,ETHUSDT")
        self.assertEqual(defaults["ENABLE_FORWARD_JOURNAL"], "1")
        self.assertEqual(defaults["ENABLE_LIVE_TELEMETRY"], "1")
        self.assertEqual(defaults["ENABLE_LIVE_HEARTBEAT"], "1")

        profiles = json.loads(defaults["PAIR_PROFILES_JSON"])
        self.assertEqual(profiles["BTCUSDT"]["min_score"], 78)
        self.assertEqual(profiles["ETHUSDT"]["min_score"], 80)
        self.assertEqual(profiles["BTCUSDT"]["regime_blocklist"], "EXPANSION,CONTRACTION,TREND")

    def test_apply_phase4_defaults_preserves_existing_values_unless_forced(self) -> None:
        with patch.dict(os.environ, {"DATA_SOURCE": "custom", "PAIRS": "BTCUSDT"}, clear=True):
            applied = apply_phase4_defaults(force=False)
            self.assertEqual(os.environ["DATA_SOURCE"], "custom")
            self.assertEqual(os.environ["PAIRS"], "BTCUSDT")
            self.assertNotIn("DATA_SOURCE", applied)
            self.assertIn("ENABLE_FORWARD_JOURNAL", applied)

            forced = apply_phase4_defaults(force=True)
            self.assertEqual(os.environ["DATA_SOURCE"], "ccxt")
            self.assertEqual(os.environ["PAIRS"], "BTCUSDT,ETHUSDT")
            self.assertIn("DATA_SOURCE", forced)

    def test_settings_can_load_without_telegram_for_dry_validation(self) -> None:
        with patch("config._load_env_file", lambda path=None: None), patch.dict(
            os.environ,
            phase4_default_env(),
            clear=True,
        ):
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            os.environ.pop("TELEGRAM_CHAT_ID", None)
            settings = Settings.from_env(require_telegram=False)
            self.assertEqual(settings.data_source, "ccxt")
            self.assertEqual(settings.pairs, ["BTCUSDT", "ETHUSDT"])
            self.assertTrue(settings.enable_forward_journal)

    def test_ccxt_feed_health_uses_trigger_timeframe_freshness(self) -> None:
        with TemporaryDirectory() as tmpdir:
            diagnostics_path = Path(tmpdir) / "market_data.jsonl"
            observed_at = datetime.now(timezone.utc).isoformat()
            rows = [
                {
                    "type": "market_data_fetch",
                    "observed_at": observed_at,
                    "data_source": "ccxt",
                    "pair": "BTCUSDT",
                    "timeframe": "M5",
                    "ok": True,
                    "slow": False,
                    "stale": False,
                },
                {
                    "type": "market_data_fetch",
                    "observed_at": observed_at,
                    "data_source": "ccxt",
                    "pair": "ETHUSDT",
                    "timeframe": "M5",
                    "ok": True,
                    "slow": False,
                    "stale": False,
                },
                {
                    "type": "market_data_fetch",
                    "observed_at": observed_at,
                    "data_source": "ccxt",
                    "pair": "BTCUSDT",
                    "timeframe": "H1",
                    "ok": True,
                    "slow": False,
                    "stale": True,
                },
            ]
            diagnostics_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            env = {
                **phase4_default_env(),
                "MARKET_DATA_DIAGNOSTICS_LOG_PATH": str(diagnostics_path),
                "ENABLE_FEED_HEALTH_CHECKS": "1",
            }

            with patch("config._load_env_file", lambda path=None: None), patch.dict(os.environ, env, clear=True):
                settings = Settings.from_env(require_telegram=False)
                components = build_feed_health_components(settings)

            ccxt = next(item for item in components if item["name"] == "ccxt_market_data")
            self.assertTrue(ccxt["ok"])
            self.assertEqual(ccxt["details"]["stale_trigger_fetches"], 0)
            self.assertEqual(ccxt["details"]["stale_by_timeframe"], {"H1": 1})


if __name__ == "__main__":
    unittest.main()
