"""
Telegram -> 24-hour Streaming Link Proxy
=========================================

Kaam kya karta hai:
1. Aap bot ko koi video forward karte ho.
2. Bot us video ka ek unique streaming link bana kar bhej deta hai.
3. Ye link 24 ghante tak valid rehta hai (SQLite me expiry save hoti hai).
4. Link kholne par video seedha stream hoti hai (Telegram se live pull karke) -
   server par kabhi bhi poori file disk pe save/download nahi hoti.

IMPORTANT / limitation (honesty ke liye):
- Hum server-side par video ko kabhi save nahi karte, aur link ko "inline"
  serve karte hain (attachment/download header set nahi karte), isliye
  browser me ye seedha video player me khulegi, "download karo" wala
  prompt nahi aayega.
- Lekin koi bhi HTTP stream ko tools (yt-dlp, browser dev tools, etc.) se
  save kar sakta hai agar user determined ho. 100% "download-proof" video
  streaming possible nahi hai jab tak DRM na ho. Ye system sirf casual
  download rokta hai, hard security guarantee nahi deta.
"""

import asyncio
import math
import os
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, PlainTextResponse
from telethon import TelegramClient, events

# ---------------------------------------------------------------------------
# Config (Render me environment variables ke through set karna)
# ---------------------------------------------------------------------------
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
LINK_TTL_SECONDS = int(os.environ.get("LINK_TTL_SECONDS", 24 * 60 * 60))  # 24h default

DB_PATH = os.environ.get("DB_PATH", "links.db")
PART_SIZE = 512 * 1024  # Telegram chunk size (256KB ka multiple; 512KB safe hai)

VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".3gp")

client = TelegramClient("bot_session", API_ID, API_HASH)


# ---------------------------------------------------------------------------
# Tiny SQLite "database" - sirf token -> (chat_id, message_id, expiry) map
# ---------------------------------------------------------------------------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS links (
            token TEXT PRIMARY KEY,
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            file_name TEXT,
            file_size INTEGER,
            mime_type TEXT,
            created_at INTEGER,
            expires_at INTEGER
        )
        """
    )
    return conn


def save_link(token, chat_id, message_id, file_name, file_size, mime_type):
    now = int(time.time())
    conn = db()
    conn.execute(
        "INSERT INTO links (token, chat_id, message_id, file_name, file_size, "
        "mime_type, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?)",
        (token, chat_id, message_id, file_name, file_size, mime_type, now,
         now + LINK_TTL_SECONDS),
    )
    conn.commit()
    conn.close()


def get_link(token):
    conn = db()
    row = conn.execute(
        "SELECT chat_id, message_id, file_name, file_size, mime_type, expires_at "
        "FROM links WHERE token=?",
        (token,),
    ).fetchone()
    conn.close()
    return row


def cleanup_expired():
    conn = db()
    conn.execute("DELETE FROM links WHERE expires_at < ?", (int(time.time()),))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Telegram bot side: forward video -> get link
# ---------------------------------------------------------------------------
@client.on(events.NewMessage(incoming=True))
async def on_message(event):
    msg = event.message
    if not msg.file:
        if msg.text and msg.text.startswith("/start"):
            await event.reply(
                "Namaste! Mujhe koi bhi video forward/send karo, main aapko "
                "24 ghante ka streaming link bana kar dunga."
            )
        return

    file_name = msg.file.name or "video.mp4"
    mime_type = msg.file.mime_type or "application/octet-stream"
    file_size = msg.file.size

    is_video = mime_type.startswith("video/") or file_name.lower().endswith(VIDEO_EXTENSIONS)
    if not is_video:
        await event.reply("⚠️ Sirf video files support hoti hain.")
        return

    token = uuid.uuid4().hex
    save_link(token, event.chat_id, msg.id, file_name, file_size, mime_type)
    link = f"{BASE_URL}/stream/{token}"

    hours = LINK_TTL_SECONDS // 3600
    await event.reply(
        f"✅ Streaming link taiyar hai ({hours} ghante valid):\n\n{link}\n\n"
        f"Isse VLC ya browser me directly khol sakte ho."
    )


# ---------------------------------------------------------------------------
# FastAPI side: serve the video with HTTP Range support (seek-friendly)
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db()
    conn.close()
    await client.start(bot_token=BOT_TOKEN)
    bot_task = asyncio.create_task(client.run_until_disconnected())
    yield
    bot_task.cancel()
    await client.disconnect()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def root():
    return PlainTextResponse("Bot + streaming server is running.")


@app.get("/stream/{token}")
async def stream(token: str, request: Request):
    cleanup_expired()
    row = get_link(token)
    if not row:
        raise HTTPException(404, "Link invalid ya expire ho chuka hai.")

    chat_id, message_id, file_name, file_size, mime_type, expires_at = row
    if time.time() > expires_at:
        raise HTTPException(410, "Ye link 24 ghante ke baad expire ho gaya hai.")

    message = await client.get_messages(chat_id, ids=message_id)
    if not message or not message.file:
        raise HTTPException(404, "Original file ab Telegram par available nahi hai.")

    # ---- Parse Range header (for seeking / partial content) ----
    range_header = request.headers.get("range")
    start = 0
    end = file_size - 1
    status_code = 200

    if range_header:
        try:
            unit, rng = range_header.split("=")
            start_str, end_str = rng.split("-")
            start = int(start_str) if start_str else 0
            end = int(end_str) if end_str else file_size - 1
        except Exception:
            start, end = 0, file_size - 1
        end = min(end, file_size - 1)
        status_code = 206

    content_length = end - start + 1

    async def body():
        # Telegram chunks sirf part_size-aligned offsets se milte hain,
        # isliye pehle aligned offset se download start karke,
        # first/last chunk ko trim karte hain taaki exact byte-range mile.
        first_part = start // PART_SIZE
        first_cut = start % PART_SIZE
        last_byte = end
        aligned_offset = first_part * PART_SIZE
        remaining = content_length
        cut = first_cut

        async for chunk in client.iter_download(
            message.media,
            offset=aligned_offset,
            request_size=PART_SIZE,
        ):
            data = bytes(chunk)
            if cut:
                data = data[cut:]
                cut = 0
            if len(data) > remaining:
                data = data[:remaining]
            if not data:
                break
            remaining -= len(data)
            yield data
            if remaining <= 0:
                break

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Type": mime_type,
        # Attachment header set NAHI karte -> browser inline play karega,
        # download prompt nahi aayega.
        "Content-Disposition": f'inline; filename="{file_name}"',
        "Cache-Control": "no-store",
    }

    return StreamingResponse(body(), status_code=status_code, headers=headers)
