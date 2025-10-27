import logging
import asyncio
import re
from datetime import datetime
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait
from pyrogram.errors.exceptions.bad_request_400 import (
    ChannelInvalid,
    ChatAdminRequired,
    UsernameInvalid,
    UsernameNotModified,
)
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from info import ADMINS
from info import INDEX_REQ_CHANNEL as LOG_CHANNEL
from database.ia_filterdb import save_file
from utils import temp

# -------------------------
# Configurable runtime options
# -------------------------
CHUNK_SIZE = 100        # messages per batch (reduce to avoid FloodWait)
BATCH_SLEEP = 0.5       # seconds to sleep after each batch (0 = no sleep)
ENABLE_FILE_LOG = False # write progress to /tmp/index_progress.log if True
# -------------------------

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

if ENABLE_FILE_LOG:
    fh = logging.FileHandler("/tmp/index_progress.log")
    fh.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

lock = asyncio.Lock()

# regex to detect t.me / telegram.me / telegram.dog links (including /c/ style)
TME_RE = re.compile(
    r"^(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/(c/)?([a-zA-Z0-9_]+)(?:/(\d+))?$",
    re.IGNORECASE,
)


def parse_tme_link(text: str):
    """
    Parse t.me style links.
    Returns (chat_id_or_username, message_id) or (None, None)
    For /c/ numeric IDs -> converted to -100{number} (int)
    For usernames -> returns username string (without @)
    """
    if not text:
        return None, None
    m = TME_RE.search(text.strip())
    if not m:
        return None, None
    is_c = bool(m.group(1))
    identifier = m.group(2)
    msg_id = m.group(3)
    if msg_id:
        try:
            msg_id = int(msg_id)
        except Exception:
            msg_id = None

    if is_c:
        if identifier.isnumeric():
            try:
                return int(f"-100{identifier}"), msg_id
            except Exception:
                return None, None
        return None, None

    if identifier.isnumeric():
        try:
            return int(identifier), msg_id
        except Exception:
            return None, None

    # username
    return identifier, msg_id


@Client.on_callback_query(filters.regex(r"^index"))
async def index_files(bot: Client, query):
    """
    Callback format: 'index#<action>#<chat>#<lst_msg_id>#<from_user>'
    action: accept | reject
    """
    data = query.data or ""
    if data.startswith("index_cancel"):
        temp.CANCEL = True
        return await query.answer("Cancelling Indexing", show_alert=False)

    parts = data.split("#")
    if len(parts) != 5:
        return await query.answer("Invalid index callback data", show_alert=True)

    _, action, chat, lst_msg_id, from_user = parts

    if action == "reject":
        try:
            await query.message.delete()
        except Exception:
            pass
        try:
            await bot.send_message(
                int(from_user),
                f"Your Submission for indexing {chat} has been declined by our moderators.",
                reply_to_message_id=int(lst_msg_id) if lst_msg_id.isnumeric() else None,
            )
        except Exception:
            pass
        return await query.answer("Rejected", show_alert=False)

    if lock.locked():
        return await query.answer("Wait until previous process completes.", show_alert=True)

    await query.answer("Processing...⏳", show_alert=False)

    # Notify submitter if not admin
    try:
        if int(from_user) not in ADMINS:
            await bot.send_message(
                int(from_user),
                f"Your Submission for indexing {chat} has been accepted by our moderators and will be added soon.",
                reply_to_message_id=int(lst_msg_id) if lst_msg_id.isnumeric() else None,
            )
    except Exception:
        pass

    # Edit moderator message to show starting progress and cancel button
    try:
        await query.message.edit(
            "Starting Indexing",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Cancel", callback_data="index_cancel")]]
            ),
        )
    except Exception:
        pass

    # coerce chat if possible
    try:
        target_chat = int(chat)
    except Exception:
        target_chat = chat

    try:
        lst_msg_id_int = int(lst_msg_id)
    except Exception:
        lst_msg_id_int = None

    await index_files_to_db(lst_msg_id_int, target_chat, query.message, bot)


@Client.on_message(
    (filters.forwarded | (filters.text & filters.regex(r"(https?://)?(t\.me|telegram\.me|telegram\.dog)"))) & filters.private & filters.incoming
)
async def send_for_index(bot: Client, message):
    """
    Accept forwarded channel messages or t.me links in private messages.
    """
    chat_id = None
    last_msg_id = None

    # parse t.me link if present
    if message.text:
        parsed_chat, parsed_msg = parse_tme_link(message.text)
        if parsed_chat is not None:
            chat_id = parsed_chat
            last_msg_id = parsed_msg

    # fallback: forwarded message info (forward_from_chat + forward_from_message_id)
    if chat_id is None and message.forward_from_chat:
        try:
            chat = message.forward_from_chat
            if getattr(chat, "type", None) == enums.ChatType.CHANNEL:
                chat_id = chat.username or chat.id
            else:
                chat_id = chat.id
            last_msg_id = message.forward_from_message_id
        except Exception:
            pass

    if not chat_id or not last_msg_id:
        return await message.reply("Invalid submission: provide a valid t.me link or forward a channel message (with message id).")

    # validate accessibility
    try:
        await bot.get_chat(chat_id)
    except ChannelInvalid:
        return await message.reply("This may be a private channel/group. Make me an admin there to index the files.")
    except (UsernameInvalid, UsernameNotModified):
        return await message.reply("Invalid Link specified.")
    except Exception as e:
        logger.exception(e)
        return await message.reply(f"Error while checking chat: {e}")

    # check message exists / accessible
    try:
        k = await bot.get_messages(chat_id, last_msg_id)
    except Exception:
        return await message.reply("Make sure that I am an admin in the channel (if channel is private) or the message exists.")
    if not k:
        return await message.reply("That message was not found or is inaccessible. Maybe the channel is private or the message was deleted.")

    # If submitter is admin -> quick accept buttons
    if message.from_user.id in ADMINS:
        buttons = [
            [
                InlineKeyboardButton("Yes", callback_data=f"index#accept#{chat_id}#{last_msg_id}#{message.from_user.id}")
            ],
            [InlineKeyboardButton("Close", callback_data="close_data")],
        ]
        return await message.reply(
            f"Do you want to index this channel/group?\n\nChat ID/Username: <code>{chat_id}</code>\nLast Message ID: <code>{last_msg_id}</code>",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    # Non-admin: prepare invite link for moderators if possible
    invite_link = None
    if isinstance(chat_id, int):
        try:
            invite_link = (await bot.create_chat_invite_link(chat_id)).invite_link
        except ChatAdminRequired:
            return await message.reply("Make sure I am an admin in the chat and have permission to invite users.")
        except Exception:
            invite_link = None
    else:
        invite_link = f"@{str(chat_id).lstrip('@')}"

    buttons = [
        [
            InlineKeyboardButton("Accept Index", callback_data=f"index#accept#{chat_id}#{last_msg_id}#{message.from_user.id}")
        ],
        [
            InlineKeyboardButton("Reject Index", callback_data=f"index#reject#{chat_id}#{message.id}#{message.from_user.id}")
        ],
    ]
    try:
        await bot.send_message(
            LOG_CHANNEL,
            f"#IndexRequest\n\nBy : {message.from_user.mention} (<code>{message.from_user.id}</code>)\nChat ID/Username - <code>{chat_id}</code>\nLast Message ID - <code>{last_msg_id}</code>\nInviteLink - {invite_link}",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except Exception:
        logger.exception("Failed to send index request to log channel.")
    await message.reply("Thank you for the contribution. Wait for my moderators to verify the files.")


@Client.on_message(filters.command("setskip") & filters.user(ADMINS))
async def set_skip_number(bot: Client, message):
    if " " in message.text:
        _, skip = message.text.split(" ", 1)
        try:
            skip = int(skip.strip())
        except Exception:
            return await message.reply("Skip number should be an integer.")
        temp.CURRENT = int(skip)
        return await message.reply(f"Successfully set SKIP number as {skip}")
    return await message.reply("Give me a skip number")


async def index_files_to_db(lst_msg_id, chat, msg, bot: Client):
    """
    Core indexing logic (walks messages from lst_msg_id downward).
    Uses FW1 behavior on FloodWait (sleep and retry).
    """
    total_files = 0
    duplicate = 0
    errors = 0
    deleted = 0
    no_media = 0
    unsupported = 0

    async with lock:
        temp.CANCEL = False
        current_pointer = temp.CURRENT if hasattr(temp, "CURRENT") and isinstance(temp.CURRENT, int) else 0

        # determine starting message id if not provided
        if not lst_msg_id:
            try:
                last = await bot.get_history(chat, limit=1)
                if last and len(last) > 0:
                    lst_msg_id = getattr(last[0], "message_id", getattr(last[0], "id", None))
            except Exception:
                lst_msg_id = None

        if not lst_msg_id:
            return await msg.edit("Could not determine the starting message id for indexing.")

        offset_id = lst_msg_id
        chunk_size = int(CHUNK_SIZE) if CHUNK_SIZE and isinstance(CHUNK_SIZE, int) else 100

        try:
            while offset_id and offset_id > 0 and not temp.CANCEL:
                # iterate messages older than or equal to offset_id (descending)
                try:
                    async for message in bot.iter_messages(chat, offset_id=offset_id, limit=chunk_size, reverse=False):
                        if temp.CANCEL:
                            break

                        current_pointer += 1

                        # periodic progress update
                        if current_pointer % 32 == 0:
                            try:
                                await msg.edit_text(
                                    text=f"Total messages fetched: <code>{current_pointer}</code>\n"
                                         f"Total files saved: <code>{total_files}</code>\n"
                                         f"Duplicate Files Skipped: <code>{duplicate}</code>\n"
                                         f"Deleted Messages Skipped: <code>{deleted}</code>\n"
                                         f"Non-Media messages skipped: <code>{no_media + unsupported}</code> (Unsupported Media - `{unsupported}`)\n"
                                         f"Errors Occurred: <code>{errors}</code>",
                                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="index_cancel")]]),
                                )
                            except Exception:
                                pass

                        if not message:
                            deleted += 1
                            continue

                        this_msg_id = getattr(message, "message_id", getattr(message, "id", None))
                        if this_msg_id is None:
                            deleted += 1
                            continue

                        # deleted message
                        if getattr(message, "empty", False):
                            deleted += 1
                            offset_id = this_msg_id - 1
                            continue

                        # no media
                        if not getattr(message, "media", None):
                            no_media += 1
                            offset_id = this_msg_id - 1
                            continue

                        # accept only AUDIO / VIDEO / DOCUMENT
                        allowed = {
                            enums.MessageMediaType.VIDEO,
                            enums.MessageMediaType.AUDIO,
                            enums.MessageMediaType.DOCUMENT,
                        }
                        if message.media not in allowed:
                            unsupported += 1
                            offset_id = this_msg_id - 1
                            continue

                        # get the actual media object
                        media_attr = getattr(message.media, "value", None)
                        media = getattr(message, media_attr, None) if media_attr else None
                        if not media:
                            media = getattr(message, "video", None) or getattr(message, "audio", None) or getattr(message, "document", None)
                        if not media:
                            unsupported += 1
                            offset_id = this_msg_id - 1
                            continue

                        # attach expected metadata
                        media.file_type = getattr(message.media, "value", None)
                        media.caption = getattr(message, "caption", None)

                        # attempt to save (handle FloodWait by sleeping and retrying once)
                        try:
                            aynav, vnay = await save_file(media)
                        except FloodWait as e:
                            # FW1: sleep and retry
                            secs = None
                            for attr in ("value", "x", "seconds", "wait", "retry_after"):
                                secs = getattr(e, attr, None)
                                if secs:
                                    break
                            try:
                                secs = int(secs)
                            except Exception:
                                try:
                                    secs = int("".join([c for c in str(e) if c.isdigit()]) or 10)
                                except:
                                    secs = 10
                            logger.warning(f"FloodWait triggered. Sleeping for {secs} seconds...")
                            await asyncio.sleep(secs)
                            try:
                                aynav, vnay = await save_file(media)
                            except Exception as exc:
                                logger.exception("Error saving after FloodWait: %s", exc)
                                errors += 1
                                offset_id = this_msg_id - 1
                                continue
                        except Exception as exc:
                            logger.exception("Error saving file: %s", exc)
                            errors += 1
                            offset_id = this_msg_id - 1
                            continue

                        if aynav:
                            total_files += 1
                        else:
                            if vnay == 0:
                                duplicate += 1
                            elif vnay == 2:
                                errors += 1

                        offset_id = this_msg_id - 1

                    # end of batch iteration

                    if temp.CANCEL:
                        await msg.edit(
                            f"Successfully Cancelled!!\n\nSaved <code>{total_files}</code> files to database!\n"
                            f"Duplicate Files Skipped: <code>{duplicate}</code>\n"
                            f"Deleted Messages Skipped: <code>{deleted}</code>\n"
                            f"Non-Media messages skipped: <code>{no_media + unsupported}</code> (Unsupported Media - `{unsupported}` )\n"
                            f"Errors Occurred: <code>{errors}</code>"
                        )
                        break

                    if not offset_id or offset_id <= 0:
                        break

                    # optional gentler sleep between batches
                    if BATCH_SLEEP and isinstance(BATCH_SLEEP, (int, float)) and BATCH_SLEEP > 0:
                        await asyncio.sleep(float(BATCH_SLEEP))

                except FloodWait as e:
                    # FW1 for batch-level FloodWait
                    secs = None
                    for attr in ("value", "x", "seconds", "wait", "retry_after"):
                        secs = getattr(e, attr, None)
                        if secs:
                            break
                    try:
                        secs = int(secs)
                    except Exception:
                        try:
                            secs = int("".join([c for c in str(e) if c.isdigit()]) or 10)
                        except:
                            secs = 10
                    logger.warning(f"FloodWait during batch iteration. Sleeping for {secs} seconds...")
                    await asyncio.sleep(secs)
                    continue

            # finished main while loop
        except Exception as e:
            logger.exception("Indexing error: %s", e)
            try:
                await msg.edit(f"Error during indexing: {e}")
            except Exception:
                pass
        else:
            try:
                await msg.edit(
                    f"Successfully saved <code>{total_files}</code> to database!\n"
                    f"Duplicate Files Skipped: <code>{duplicate}</code>\n"
                    f"Deleted Messages Skipped: <code>{deleted}</code>\n"
                    f"Non-Media messages skipped: <code>{no_media + unsupported}</code> (Unsupported Media - `{unsupported}` )\n"
                    f"Errors Occurred: <code>{errors}</code>"
                )
            except Exception:
                pass

        # final optional file log
        if ENABLE_FILE_LOG:
            try:
                logger.info(
                    f"Index finished. Saved:{total_files} duplicates:{duplicate} deleted:{deleted} non_media:{no_media} unsupported:{unsupported} errors:{errors}"
                )
            except Exception:
                pass
