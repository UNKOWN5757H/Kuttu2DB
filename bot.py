import logging
import logging.config
import asyncio
import os
import sys
import re
from datetime import datetime, timedelta

# Pyrogram and other imports
import tgcrypto
from pyrogram import Client, __version__, types
from pyrogram.raw.all import layer
from typing import Union, Optional, AsyncGenerator

# Database modules
from database.ia_filterdb import Media, Media2, choose_mediaDB, db as clientDB
from database.users_chats_db import db
from info import (
    SESSION,
    API_ID,
    API_HASH,
    BOT_TOKEN,
    LOG_STR,
    LOG_CHANNEL,
    SECONDDB_URI,
    DATABASE_URI,
    RESTART_INTERVAL,
)
from utils import temp
from sample_info import tempDict

# aiohttp web for webserver
from aiohttp import web as webserver
from plugins.webcode import bot_run
from os import environ

# Prevent asyncio logging spam
logging.getLogger("asyncio").setLevel(logging.CRITICAL - 1)

# Peer ID invalid fix
from pyrogram import utils as pyroutils
pyroutils.MIN_CHAT_ID = -999999999999
pyroutils.MIN_CHANNEL_ID = -100999999999999

# Load logging config
logging.config.fileConfig("logging.conf")
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("imdbpy").setLevel(logging.ERROR)

PORT_CODE = environ.get("PORT", "8080")


class Bot(Client):
    def __init__(self):
        super().__init__(
            name=SESSION,
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            workers=50,
            plugins={"root": "plugins"},
            sleep_threshold=5,
        )

    async def start(self):
        # Load banned users/chats
        b_users, b_chats = await db.get_banned()
        temp.BANNED_USERS = b_users
        temp.BANNED_CHATS = b_chats

        await super().start()

        # Ensure indexes in DBs
        await Media.ensure_indexes()
        await Media2.ensure_indexes()

        # Check DB space and choose DB
        stats = await clientDB.command("dbStats")
        free_dbSize = round(
            512
            - ((stats["dataSize"] / (1024 * 1024)) + (stats["indexSize"] / (1024 * 1024))),
            2,
        )
        if SECONDDB_URI and free_dbSize < 350:
            tempDict["indexDB"] = SECONDDB_URI
            logging.info(
                f"Since Primary DB has only {free_dbSize} MB left, Secondary DB will be used."
            )
        elif SECONDDB_URI is None:
            logging.error("Missing second DB URI! Add SECONDDB_URI now. Exiting...")
            exit()
        else:
            logging.info(f"Primary DB has enough space ({free_dbSize} MB), continuing with it.")

        await choose_mediaDB()

        # Get bot info
        me = await self.get_me()
        temp.ME = me.id
        temp.U_NAME = me.username
        temp.B_NAME = me.first_name
        self.username = "@" + me.username
        logging.info(f"{me.first_name} (Pyrogram v{__version__}, Layer {layer}) started on @{me.username}.")

        # Log channel message
        try:
            await self.send_message(
                chat_id=LOG_CHANNEL,
                text="✅ Bot Started Successfully!\n⚡ Kuttu Bot2 with 2DB Feature Active."
            )
        except Exception as e:
            logging.error(f"Could not send start message: {e}")

        print("⚡ Og Eva Re-edited — 2 DB System Initialized ⚡")

        # Run web server
        client = webserver.AppRunner(await bot_run())
        await client.setup()
        bind_address = "0.0.0.0"
        await webserver.TCPSite(client, bind_address, PORT_CODE).start()

        # Schedule restart
        asyncio.create_task(self.schedule_restart(RESTART_INTERVAL))

    async def stop(self, *args):
        await super().stop()
        logging.info("Bot stopped. Bye 👋")

    async def restart(self):
        logging.info("Restarting bot process...")
        await self.stop()
        # Koyeb/Docker compatible restart
        os._exit(0)

    async def schedule_restart(self, interval: str = RESTART_INTERVAL):
        """
        Automatically restart the bot after the given interval.
        Example interval: '12h', '1d', '30m'
        """
        if not interval:
            logging.warning("No restart interval set — skipping auto-restart.")
            return

        try:
            seconds = parse_interval(interval)
        except Exception as e:
            logging.error(f"Invalid restart interval '{interval}': {e}")
            return

        while True:
            try:
                # Sleep until 1 minute before restart
                await asyncio.sleep(max(0, seconds - 60))
                try:
                    await self.send_message(
                        chat_id=LOG_CHANNEL,
                        text=f"⚠️ Bot will restart in 1 minute (every {interval}).",
                    )
                except Exception as e:
                    logging.error(f"Could not send restart warning: {e}")

                await asyncio.sleep(60)
                await self.restart()
            except Exception as e:
                logging.error(f"Restart loop error: {e}")
                await asyncio.sleep(60)

    async def iter_messages(
        self,
        chat_id: Union[int, str],
        limit: int,
        offset: int = 0,
    ) -> Optional[AsyncGenerator["types.Message", None]]:
        """
        Custom message iterator that fetches messages in chunks.
        """
        current = offset
        while True:
            new_diff = min(200, limit - current)
            if new_diff <= 0:
                return
            messages = await self.get_messages(
                chat_id, list(range(current, current + new_diff + 1))
            )
            for message in messages:
                yield message
                current += 1


# Helper function: Parse restart interval
def parse_interval(interval: str) -> int:
    """
    Convert interval string like '1h', '2d', '30m' to seconds.
    """
    match = re.match(r"(\d+)([dhm])", interval.lower())
    if not match:
        raise ValueError("Invalid interval format. Use e.g., '1h', '2d', '30m'.")
    value, unit = match.groups()
    value = int(value)
    if unit == "d":
        return value * 24 * 60 * 60
    elif unit == "h":
        return value * 60 * 60
    elif unit == "m":
        return value * 60
    else:
        raise ValueError("Invalid time unit. Only 'd', 'h', 'm' are allowed.")


# Run bot
app = Bot()
app.run()
