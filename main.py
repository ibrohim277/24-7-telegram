"""
Telegram profilni 24/7 "online" holatda ushlab turuvchi skript (userbot).

Ishlatish:
    1. https://my.telegram.org -> API development tools -> api_id va api_hash oling
    2. Muhit o'zgaruvchilarini o'rnating (yoki README.md ga qarang):
         TG_API_ID, TG_API_HASH
    3. python main.py
       Birinchi ishga tushirishda telefon raqam va Telegram kodi so'raladi.
"""

import asyncio
import logging
import os

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.functions.account import UpdateStatusRequest

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
SESSION = os.environ.get("TG_SESSION", "online_session")
INTERVAL = int(os.environ.get("PING_INTERVAL", "30"))  # soniya

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("keep-online")


async def keep_online(client: TelegramClient) -> None:
    while True:
        try:
            if not client.is_connected():
                await client.connect()
            await client(UpdateStatusRequest(offline=False))
            log.info("Status: online")
            await asyncio.sleep(INTERVAL)
        except FloodWaitError as e:
            log.warning("FloodWait: %s soniya kutiladi", e.seconds)
            await asyncio.sleep(e.seconds + 1)
        except Exception as e:  # tarmoq uzilishi va h.k.
            log.error("Xato: %s. 15 soniyadan keyin qayta uriniladi", e)
            await asyncio.sleep(15)


async def main() -> None:
    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.start()  # birinchi marta: telefon + kod so'raydi
    me = await client.get_me()
    log.info("Kirildi: %s (@%s)", me.first_name, me.username)
    await keep_online(client)


if __name__ == "__main__":
    asyncio.run(main())