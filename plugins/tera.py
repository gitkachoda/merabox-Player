import os
import tempfile
import requests
import asyncio
from urllib.parse import urlencode, urlparse, parse_qs
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from config import CHANNEL, DATABASE
from verify_patch import IS_VERIFY, is_verified, build_verification_link, HOW_TO_VERIFY

# MongoDB connection setup
print("[LOG] Connecting to MongoDB...")
mongo_client = MongoClient(DATABASE.URI)
db = mongo_client[DATABASE.NAME]
settings_col = db["terabox_settings"]
queue_col = db["terabox_queue"]
last_upload_col = db["terabox_lastupload"]

TERABOX_REGEX = r'https?://(?:www\.)?[^/\s]*tera[^/\s]*\.[a-z]+/s/[^\s]+'
COOKIE = "ndus=YzYvy3xteHuiCt2sBHXdwcE-7F7QaIvyWRKfIMqU"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
    "Connection": "keep-alive",
    "DNT": "1",
    "Host": "www.terabox.app",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/135.0.0.0 Safari/537.36 Edg/135.0.0.0",
    "sec-ch-ua": '"Microsoft Edge";v="135", "Not-A.Brand";v="8", "Chromium";v="135"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cookie": COOKIE,
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

DL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.terabox.com/",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Cookie": COOKIE,
}

def get_size(b: int) -> str:
    return (f"{b/1024**3:.2f} GB" if b >= 1024**3 else
            f"{b/1024**2:.2f} MB" if b >= 1024**2 else
            f"{b/1024:.2f} KB" if b >= 1024 else
            f"{b} bytes")

def find_between(text: str, start: str, end: str) -> str:
    try: return text.split(start, 1)[1].split(end, 1)[0]
    except Exception: return ""

def get_file_info(url: str) -> dict:
    resp = requests.get(url, headers=HEADERS, allow_redirects=True)
    if resp.status_code != 200:
        raise ValueError(f"Failed to fetch share page ({resp.status_code})")
    final_url = resp.url
    surl = parse_qs(urlparse(final_url).query).get("surl", [None])[0]
    if not surl:
        raise ValueError("Invalid share URL (missing surl)")
    html = requests.get(final_url, headers=HEADERS).text
    js_token = find_between(html, 'fn%28%22', '%22%29')
    logid    = find_between(html, 'dp-logid=', '&')
    bdstoken = find_between(html, 'bdstoken":"', '"')
    if not all([js_token, logid, bdstoken]):
        raise ValueError("Failed to extract authentication tokens")

    params = {
        "app_id":"250528","web":"1","channel":"dubox","clienttype":"0",
        "jsToken":js_token,"dp-logid":logid,"page":"1","num":"20","by":"name",
        "order":"asc","site_referer":final_url,"shorturl":surl,"root":"1,"
    }
    info = requests.get("https://www.terabox.app/share/list?" + urlencode(params), headers=HEADERS).json()
    if info.get("errno") or not info.get("list"):
        raise ValueError(f"List API error: {info.get('errmsg','Unknown error')}")

    file = info["list"][0]
    size = int(file.get("size",0))
    return {
        "name":file.get("server_filename","download"),
        "download_link":file.get("dlink",""),
        "size_bytes":size,
        "size_str":get_size(size)
    }

async def replace_msg(base: Message, prev: Message | None, text: str) -> Message:
    try:
        if prev: await prev.delete()
    except Exception:
        pass
    return await base.reply(text)

@Client.on_message(filters.private & filters.regex(TERABOX_REGEX))
async def handle_terabox(client, message: Message):
    user_id = message.from_user.id

    if IS_VERIFY and not await is_verified(user_id):
        verify_url = await build_verification_link(client.me.username, user_id)
        btns = [[InlineKeyboardButton("✅ Verify Now", url=verify_url),
                 InlineKeyboardButton("📖 Tutorial", url=HOW_TO_VERIFY)]]
        await message.reply_text(
            "🔐 You must verify before using this command.\n\n⏳ Verification lasts for 12 hours.",
            reply_markup=InlineKeyboardMarkup(btns)
        )
        return

    try:
        info = get_file_info(message.text.strip())
    except Exception as e:
        return await message.reply(f"❌ Failed to get file info:\n{e}")

    temp_path = os.path.join(tempfile.gettempdir(), info["name"])
    progress_msg = None

    try:
        progress_msg = await replace_msg(message, progress_msg, "📥 Downloading file...")

        with requests.get(info["download_link"], headers=DL_HEADERS, stream=True) as r:
            r.raise_for_status()
            with open(temp_path, "wb") as f:
                for chunk in r.iter_content(1024 * 1024):
                    if chunk:
                        f.write(chunk)

        progress_msg = await replace_msg(message, progress_msg, "⬆️ Sending to you...")

        caption = f"File Name: {info['name']}\nFile Size: {info['size_str']}\nLink: {message.text.strip()}"
        is_vid = any(info["name"].lower().endswith(ext) for ext in [".mp4",".mkv",".webm",".mov",".avi"])

        if is_vid:
            sent = await client.send_video(message.chat.id, temp_path, caption=caption, supports_streaming=True)
        else:
            sent = await client.send_document(message.chat.id, temp_path, caption=caption,
                                              file_name=info["name"], protect_content=True)

        await replace_msg(message, progress_msg, "✅ Done! File sent.")
        await message.reply("✅ File will be deleted from your chat after 12 hours.")
        await asyncio.sleep(43200)
        try: await sent.delete()
        except Exception: pass

    except Exception as e:
        await message.reply(f"❌ Upload failed:\n`{e}`")

    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
