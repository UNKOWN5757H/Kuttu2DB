
#!/usr/bin/env python3
"""
Refactored bot.py — full rewrite (Option 2).
Behavior notes:
- Time-based auto-restart is kept (scheduled via RESTART_INTERVAL).
- Programmatic crash-triggered restart is NOT performed (crash-restart = no).
- Supervisor / container orchestrator may still restart the process when it exits.
"""

import asyncio
import logging
import logging.config
import os
import re
import sys
from datetime import datetime
from typing import AsyncGenerator, Optional, Union

# Pyrogram & crypto
import tgcrypto
from pyrogram import Client, __version__, types
from pyrogram.raw.all import layer
from pyrogram import utils as pyroutils

# aiohttp webserver
from aiohttp import web as webserver

# Database modules and helpers (from your project)
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
from plugins.webcode import bot_run  # returns an aiohttp.web.Application
from utils import temp
from sample_info import tempDict

# -----------------------
# Minimal logging setup
# -----------------------
if os.path.exists("logging.conf"):
    try:
        logging.config.fileConfig("logging.conf", disable_existing_loggers=False)
    except Exception as e:
        logging.basicConfig(level=logging.INFO)
        logging.getLogger(__name__).exception("Failed to load logging.conf, using basicConfig: %s", e)
else:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

# Reduce asyncio spam
logging.getLogger("asyncio").setLevel(logging.CRITICAL - 1)
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("imdbpy").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)

# -----------------------
# Peer ID fix (your existing change)
# -----------------------
# extend pyrogram's minimum chat/channel id to avoid PeerIdInvalid in some cases
pyroutils.MIN_CHAT_ID = -999_999_999_999
pyroutils.MIN_CHANNEL_ID = -100_999_999_999_999

# Validate essential env/config values early
_missing = []
for name, val in (("SESSION", SESSION), ("API_ID", API_ID), ("API_HASH", API_HASH), ("BOT_TOKEN", BOT_TOKEN)):
    if not val:
        _missing.append(name)
if _missing:
    logger.critical("Missing required config: %s. Exiting.", ", ".join(_missing))
    sys.exit(1)

PORT_CODE = int(os.environ.get("PORT", os.environ.get("PORT_CODE", "8080")))


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
        # runtime info
        self._runner: Optional[webserver.AppRunner] = None
        self._site: Optional[webserver.TCPSite] = None

    # ---------- lifecycle ----------
    async def start(self):
        """
        Start the bot: login, DB setup, webserver, schedule restart.
        """
        # load banned lists first (non-fatal)
        try:
            b_users, b_chats = await db.get_banned()
            temp.BANNED_USERS = b_users
            temp.BANNED_CHATS = b_chats
            logger.info("Loaded banned users/chats: %d/%d", len(b_users), len(b_chats))
        except Exception as e:
            logger.warning("Failed to load banned lists: %s", e)

        # Start pyrogram client
        await super().start()

        # Ensure indexes for Media DB models
        try:
            await Media.ensure_indexes()
            await Media2.ensure_indexes()
            logger.info("Ensured DB indexes for Media collections.")
        except Exception as e:
            logger.exception("Error ensuring indexes: %s", e)

        # Decide DB selection based on free space
        try:
            stats = await clientDB.command("dbStats")
            data_mb = stats.get("dataSize", 0) / (1024 * 1024)
            index_mb = stats.get("indexSize", 0) / (1024 * 1024)
            free_dbSize = round(512 - (data_mb + index_mb), 2)
            logger.info("Primary DB free approx: %s MB", free_dbSize)

            if free_dbSize < 350:
                if SECONDDB_URI:
                    tempDict["indexDB"] = SECONDDB_URI
                    logger.info(
                        "Primary DB low (%.2f MB). Switching indexDB to SECONDDB_URI.", free_dbSize
                    )
                else:
                    logger.critical(
                        "Primary DB low (%.2f MB) and no SECONDDB_URI configured. Exiting.", free_dbSize
                    )
                    # exit cleanly; supervisor may restart image — but user requested no programmatic crash restart
                    await self.stop()
                    os._exit(1)
            else:
                logger.info("Primary DB has enough space (%.2f MB).", free_dbSize)
        except Exception as e:
            logger.exception("Failed to check DB stats: %s", e)

        # Choose the media DB (your existing function)
        try:
            await choose_mediaDB()
        except Exception as e:
            logger.exception("Failed to choose media DB: %s", e)

        # Get bot info and set basic runtime fields
        try:
            me = await self.get_me()
            temp.ME = me.id
            temp.U_NAME = me.username
            temp.B_NAME = me.first_name
            self.username = "@" + (me.username or "")
            logger.info("%s (Pyrogram v%s, Layer %s) started on %s", me.first_name, __version__, layer, self.username)
        except Exception as e:
            logger.exception("Failed to fetch bot me: %s", e)

        # Send start message if LOG_CHANNEL is configured (not fatal)
        try:
            if LOG_CHANNEL:
                await self.send_message(LOG_CHANNEL, "✅ Bot Started Successfully!\n⚡ Kuttu Bot2 with 2DB Feature Active.")
            else:
                logger.warning("LOG_CHANNEL not set; skipping start message.")
        except Exception as e:
            logger.error("Could not send start message to log channel: %s", e)

        print("⚡ Og Eva Re-edited — 2 DB System Initialized ⚡")

        # Start webserver (aiohttp Application returned by bot_run)
        try:
            app = await bot_run()
            if not isinstance(app, webserver.Application):
                raise RuntimeError("plugins.webcode.bot_run did not return aiohttp.web.Application")

            runner = webserver.AppRunner(app)
            await runner.setup()
            bind_address = "0.0.0.0"
            site = webserver.TCPSite(runner, bind_address, PORT_CODE)
            await site.start()

            # Save runner/site for graceful shutdown
            self._runner = runner
            self._site = site
            logger.info("Webserver started and listening on %s:%s", bind_address, PORT_CODE)
        except Exception as e:
            logger.exception("Failed to start aiohttp webserver: %s", e)
            # do not automatically call restart here (crash-restart = no), but stop bot to avoid partial startup
            await self.stop()
            os._exit(1)

        # Schedule the periodic restart (time-based only)
        asyncio.create_task(self.schedule_restart(RESTART_INTERVAL))

    async def stop(self, *args):
        """Stop bot and webserver gracefully."""
        logger.info("Stopping bot and webserver...")
        try:
            if self._site:
                # no direct close method on TCPSite, but runner.cleanup handles it
                pass
            if self._runner:
                await self._runner.cleanup()
                logger.info("Webserver runner cleaned up.")
        except Exception as e:
            logger.exception("Error while stopping webserver: %s", e)

        try:
            await super().stop()
        except Exception as e:
            logger.exception("Error while stopping pyrogram client: %s", e)

        logger.info("Bot stopped cleanly.")

    async def restart(self):
        """
        Programmatic restart — used only by scheduled restart.
        Exits the process so an external supervisor can restart the container.
        """
        logger.info("Restarting bot process (scheduled). Exiting with code 0.")
        # Ensure graceful stop first
        try:
            await self.stop()
        except Exception as e:
            logger.exception("Error during graceful stop before scheduled restart: %s", e)
        # Immediate process exit (supervisor will restart the container if configured).
        os._exit(0)

    # -------------------------
    # utility: message iterator
    # -------------------------
    async def iter_messages(
        self,
        chat_id: Union[int, str],
        limit: int,
        offset_id: Optional[int] = None,
    ) -> Optional[AsyncGenerator[types.Message, None]]:
        """
        Robust message iterator using get_history in chunks.
        Yields up to `limit` messages starting after offset_id (if provided).
        """
        fetched = 0
        chunk = 200 if limit > 200 else limit
        try:
            while fetched < limit:
                remaining = limit - fetched
                take = min(chunk, remaining)
                # get_history returns newest -> oldest by default; we iterate oldest->newest
                history = await self.get_history(chat_id, offset_id=offset_id, limit=take)
                if not history:
                    return
                # reverse to yield oldest first if needed
                for msg in reversed(history):
                    yield msg
                    fetched += 1
                    if fetched >= limit:
                        return
                # prepare next offset_id to fetch earlier messages
                offset_id = history[-1].message_id if history else offset_id
                if len(history) < take:
                    return
        except Exception as e:
            logger.exception("iter_messages error: %s", e)
            return

# -------------------------
# helper: parse restart interval
# -------------------------
def parse_interval(interval: Union[str, int, None]) -> int:
    """
    Accepts strings like '1h', '2d', '30m' or integer seconds.
    Returns seconds (int).
    Raises ValueError on bad format.
    """
    if interval is None:
        raise ValueError("Interval is None")
    if isinstance(interval, int):
        if interval <= 0:
            raise ValueError("Interval must be positive")
        return interval

    interval = str(interval).strip().lower()
    m = re.fullmatch(r"(\d+)([dhm])", interval)
    if not m:
        raise ValueError("Invalid interval format. Use e.g., '1h', '2d', '30m' or an integer number of seconds.")
    val = int(m.group(1))
    unit = m.group(2)
    if unit == "d":
        return val * 86400
    elif unit == "h":
        return val * 3600
    elif unit == "m":
        return val * 60
    raise ValueError("Invalid time unit.")

# -------------------------
# scheduled restart task
# -------------------------
async def schedule_restart_task(bot: Bot, interval: str):
    """
    Wrapper to keep scheduled restart logic contained; logs and exits the process.
    """
    if not interval:
        logger.info("No RESTART_INTERVAL set — scheduled restart disabled.")
        return

    try:
        seconds = parse_interval(interval)
    except Exception as e:
        logger.error("Invalid RESTART_INTERVAL '%s': %s. Skipping scheduled restart.", interval, e)
        return

    logger.info("Scheduled restart enabled: every %s (=%d seconds).", interval, seconds)
    while True:
        # sleep until 1 minute before restart (if interval < 60s, sleep full interval)
        to_sleep = max(0, seconds - 60) if seconds > 60 else seconds
        await asyncio.sleep(to_sleep)
        # notify 1 minute before if possible
        try:
            if LOG_CHANNEL:
                await bot.send_message(LOG_CHANNEL, f"⚠️ Bot will restart in 1 minute (every {interval}).")
        except Exception as e:
            logger.warning("Could not send restart warning message: %s", e)
        # final 60s (or 0) wait
        await asyncio.sleep(60 if seconds > 60 else 0)
        # call scheduled restart
        try:
            await bot.restart()
        except Exception as e:
            logger.exception("Error during scheduled restart: %s", e)
            # If restart fails, exit to allow supervisor to handle
            os._exit(1)

# -------------------------
# main entrypoint
# -------------------------
def main():
    """
    Entrypoint: constructs Bot and runs it.
    Wraps run() in try/except to log uncaught exceptions but does not programmatically restart (crash-restart=no).
    """
    bot = Bot()

    # attach schedule_restart task after start via pyrogram's idle approach
    # We'll create a background task in the event loop once running by using the on_startup hook: here we rely on start() scheduling it.

    try:
        # pyrogram Client.run handles startup and idle loop
        bot.run()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, shutting down.")
        # graceful shutdown attempt
        try:
            # .stop is async — best effort synchronous shutdown before exit
            loop = asyncio.get_event_loop()
            loop.run_until_complete(bot.stop())
        except Exception:
            pass
        sys.exit(0)
    except Exception as e:
        # Log any unhandled exception — do not auto-restart
        logger.exception("Uncaught exception in bot.run(): %s", e)
        # Try to stop gracefully
        try:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(bot.stop())
        except Exception:
            pass
        # Exit with non-zero so external orchestrator can decide to restart or not
        sys.exit(1)


if __name__ == "__main__":
    main()
