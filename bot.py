#!/usr/bin/env python3
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

# aiohttp web for webserver (your app comes from plugins.webcode.bot_run)
from aiohttp import web as webserver
from plugins.webcode import bot_run

# Prevent asyncio logging spam
logging.getLogger("asyncio").setLevel(logging.CRITICAL - 1)

# Peer ID invalid fix
from pyrogram import utils as pyroutils
pyroutils.MIN_CHAT_ID = -999999999999
pyroutils.MIN_CHANNEL_ID = -100999999999999

# Load logging config with fallback
if os.path.exists("logging.conf"):
    try:
        logging.config.fileConfig("logging.conf", disable_existing_loggers=False)
    except Exception as e:
        logging.basicConfig(level=logging.INFO)
        logging.getLogger(__name__).exception("Failed to load logging.conf, using basicConfig: %s", e)
else:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

logging.getLogger().setLevel(logging.INFO)
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("imdbpy").setLevel(logging.ERROR)

# Ensure PORT is integer and coming from env (Koyeb will inject PORT)
try:
    PORT_CODE = int(os.environ.get("PORT", os.environ.get("PORT_CODE", "8080")))
except Exception:
    PORT_CODE = 8080

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
        self._web_runner = None

    async def start(self):
        # Load banned users/chats
        try:
            b_users, b_chats = await db.get_banned()
            temp.BANNED_USERS = b_users
            temp.BANNED_CHATS = b_chats
        except Exception as e:
            logging.warning(f"Failed to load banned lists: {e}")

        await super().start()

        # Ensure indexes in DBs
        try:
            await Media.ensure_indexes()
            await Media2.ensure_indexes()
        except Exception as e:
            logging.exception("Error ensuring indexes: %s", e)

        # Check DB space and choose DB
        try:
            stats = await clientDB.command("dbStats")
            free_dbSize = round(
                512
                - ((stats.get("dataSize", 0) / (1024 * 1024)) + (stats.get("indexSize", 0) / (1024 * 1024))),
                2,
            )
        except Exception as e:
            logging.exception("Failed to read DB stats: %s", e)
            free_dbSize = 512  # optimistic fallback

        if free_dbSize < 350:
            if SECONDDB_URI:
                tempDict["indexDB"] = SECONDDB_URI
                logging.info(f"Primary DB low ({free_dbSize} MB). Using SECONDDB_URI for indexDB.")
            else:
                logging.critical(f"Primary DB low ({free_dbSize} MB) and no SECONDDB_URI set. Exiting.")
                await self.stop()
                os._exit(1)
        else:
            logging.info(f"Primary DB has enough space ({free_dbSize} MB).")

        await choose_mediaDB()

        # Get bot info
        try:
            me = await self.get_me()
            temp.ME = me.id
            temp.U_NAME = me.username
            temp.B_NAME = me.first_name
            self.username = "@" + (me.username or "")
            logging.info(f"{me.first_name} (Pyrogram v{__version__}, Layer {layer}) started on @{me.username}.")
        except Exception as e:
            logging.exception("Failed to get bot info: %s", e)

        # Log channel message
        try:
            if LOG_CHANNEL:
                await self.send_message(
                    chat_id=LOG_CHANNEL,
                    text="✅ Bot Started Successfully!\n⚡ Kuttu Bot2 with 2DB Feature Active."
                )
            else:
                logging.info("LOG_CHANNEL not set; skipping start message.")
        except Exception as e:
            logging.error(f"Could not send start message: {e}")

        print("⚡ Og Eva Re-edited — 2 DB System Initialized ⚡")

        # Run web server (get aiohttp app from your plugin)
        try:
            sub_app = await bot_run()  # expected to return aiohttp.web.Application
            if not isinstance(sub_app, webserver.Application):
                logging.error("plugins.webcode.bot_run() did not return aiohttp.web.Application. Aborting web startup.")
                raise RuntimeError("bot_run() returned non-aiohttp app")

            # Add health endpoints directly to the returned app
            async def index_handler(request):
                # return basic info; safe if temp.B_NAME missing
                return webserver.json_response({"status": "running", "bot": getattr(temp, "B_NAME", None)})

            async def health_handler(request):
                return webserver.json_response({"status": "ok"})

            # Add or overwrite routes
            try:
                sub_app.router.add_get("/", index_handler)
                sub_app.router.add_get("/healthz", health_handler)
            except RuntimeError:
                # If routes already exist or router is frozen, use add_routes fallback
                sub_app.add_routes([
                    webserver.get("/", index_handler),
                    webserver.get("/healthz", health_handler),
                ])

            runner = webserver.AppRunner(sub_app)
            await runner.setup()
            site = webserver.TCPSite(runner, "0.0.0.0", PORT_CODE)
            await site.start()
            self._web_runner = runner
            logging.info(f"Webserver started on 0.0.0.0:{PORT_CODE}")
        except Exception as e:
            logging.exception("Failed to start webserver: %s", e)
            await self.stop()
            os._exit(1)

        # Schedule restart
        try:
            asyncio.create_task(self.schedule_restart(RESTART_INTERVAL))
        except Exception as e:
            logging.exception("Failed to schedule restart: %s", e)

    async def stop(self, *args):
        # Stop web runner first
        try:
            if self._web_runner:
                await self._web_runner.cleanup()
                logging.info("Webserver runner cleaned up.")
        except Exception as e:
            logging.exception("Error cleaning up webserver: %s", e)

        await super().stop()
        logging.info("Bot stopped. Bye 👋")

    async def restart(self):
        logging.info("Restarting bot process (scheduled)...")
        await self.stop()
        # Koyeb/Docker compatible restart — orchestrator should restart the container
        os._exit(0)

    async def schedule_restart(self, interval: str = RESTART_INTERVAL):
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
                await asyncio.sleep(max(0, seconds - 60))
                try:
                    if LOG_CHANNEL:
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

# Helper: parse_interval
def parse_interval(interval: str) -> int:
    match = re.match(r"(\d+)([dhm])", str(interval).lower())
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
if __name__ == "__main__":
    # Basic quick validation for required config
    missing = [k for k, v in (("SESSION", SESSION), ("API_ID", API_ID), ("API_HASH", API_HASH), ("BOT_TOKEN", BOT_TOKEN)) if not v]
    if missing:
        logging.critical("Missing required configuration: %s", ", ".join(missing))
        sys.exit(1)

    app = Bot()
    # Client.run will call .start() and then block while running listeners
    app.run()
