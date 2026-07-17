from __future__ import annotations

import asyncio
import unittest

from aiohttp.resolver import ThreadedResolver

from services.telegram import TelegramSignalService


class TelegramSignalServiceTests(unittest.TestCase):
    def test_uses_system_dns_resolver_for_aiogram_session(self) -> None:
        async def check() -> None:
            service = TelegramSignalService(token="123456:test-token", chat_id="1")
            try:
                await service.bot.session.create_session()
                resolver = service.bot.session._connector_init.get("resolver")
                self.assertIsInstance(resolver, ThreadedResolver)
            finally:
                await service.close()

        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
