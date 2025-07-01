
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

# Regex for matching TeraBox share links
TERABOX_REGEX = r'https?://(?:www\.)?[^/\s]*tera[^/\s]*\.[a-z]+/s/[^\s]+'

# YOUR LATEST COOKIE
COOKIE = "ndus=YzYvy3xteHuiCt2sBHXdwcE-7F7QaIvyWRKfIMqU"

# Headers for requesting data
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
    "Accept": "text/html,application/xhtml+xml,application/xml;"
              "q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.terabox.com/",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Cookie": COOKIE,
}

def get_size(bytes_len: int) -> str:
    if bytes_len >= 1024 ** 3:
        return f"{bytes_len / 1024**3:.2f} GB"
    if bytes_len >= 1024 ** 2:
        return f"{bytes_len / 1024**2:.2f} MB"
    if bytes_len >= 1024:
        return f"{bytes_len / 1024:.2f} KB"
    return f"{bytes_len} bytes"

def find_between(text: str, start: str, end: str) -> str:
    try:
        return text.split(start, 1)[1].split(end, 1)[0]
    except Exception:
        return ""

def get_file_info(share_url: str) -> dict:
    print(f"[LOG] Fetching share page: {share_url}")
    resp = requests.get(share_url, headers=HEADERS, allow_redirects=True)
    if resp.status_code != 200:
        print(f"[ERROR] Failed to fetch share page ({resp.status_code})")
        raise ValueError(f"Failed to fetch share page ({resp.status_code})")
    final_url = resp.url
    print(f"[LOG] Final URL: {final_url}")

    parsed = urlparse(final_url)
    surl = parse_qs(parsed.query).get("surl", [None])[0]
    if not surl:
        print("[ERROR] Invalid share URL (missing surl)")
        raise ValueError("Invalid share URL (missing surl)")

    page = requests.get(final_url, headers=HEADERS)
    html = page.text

    js_token = find_between(html, 'fn%28%22', '%22%29')
    logid = find_between(html, 'dp-logid=', '&')
    bdstoken = find_between(html, 'bdstoken":"', '"')
    if not all([js_token, logid, bdstoken]):
        print("[ERROR] Failed to extract authentication tokens")
        raise ValueError("Failed to extract authentication tokens")

    print(f"[LOG] Extracted tokens: js_token={js_token}, logid={logid}, bdstoken={bdstoken}")

    params = {
        "app_id": "250528", "web": "1", "channel": "dubox",
        "clienttype": "0", "jsToken": js_token, "dp-logid": logid,
        "page": "1", "num": "20", "by": "name", "order": "asc",
        "site_referer": final_url, "shorturl": surl, "root": "1,",
    }
    print(f"[LOG] Requesting file info with params: {params}")
    info = requests.get(
        "https://www.terabox.app/share/list?" + urlencode(params),
        headers=HEADERS
    ).json()

    if info.get("errno") or not info.get("list"):
        errmsg = info.get("errmsg", "Unknown error")
        print(f"[ERROR] List API error: {errmsg}")
        raise ValueError(f"List API error: {errmsg}")

    file = info["list"][0]
    size_bytes = int(file.get("size", 0))
    print(f"[LOG] File info: name={file.get('server_filename', 'download')}, size={size_bytes}")
    return {
        "name": file.get("server_filename", "download"),
        "download_link": file.get("dlink", ""),
        "size_bytes": size_bytes,
        "size_str": get_size(size_bytes)
    }

@Client.on_message(filters.private & filters.regex(TERABOX_REGEX))
async def handle_terabox(client, message: Message):
    user_id = message.from_user.id
    print(f"[LOG] Received message from user: {user_id}")

    if IS_VERIFY and not await is_verified(user_id):
        print("[LOG] User not verified, sending verification link.")
        verify_url = await build_verification_link(client.me.username, user_id)
        buttons = [
            [
                InlineKeyboardButton("✅ Verify Now", url=verify_url),
                InlineKeyboardButton("📖 Tutorial", url=HOW_TO_VERIFY)
            ]
        ]
        await message.reply_text(
            "🔐 You must verify before using this command.\n\n⏳ Verification lasts for 12 hours.",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    url = message.text.strip()
    print(f"[LOG] Processing URL: {url}")
    try:
        info = get_file_info(url)
    except Exception as e:
        print(f"[ERROR] Failed to get file info: {e}")
        return await message.reply(f"❌ Failed to get file info:\n{e}")

    temp_path = os.path.join(tempfile.gettempdir(), info["name"])
    print(f"[LOG] Temp path for download: {temp_path}")

    try:
        print(f"[LOG] Starting download: {info['download_link']}")
        with requests.get(info["download_link"], headers=DL_HEADERS, stream=True) as r:
            r.raise_for_status()
            total_size = int(r.headers.get("content-length", 0))
            downloaded = 0
            chunk_size = 1024 * 1024  # 1MB
            bar_length = 20
            last_percent = 0
            progress_msg = await message.reply("📥 Download started...")

            with open(temp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)

                        percent_float = downloaded / total_size
                        percent = percent_float * 100
                        filled = int(bar_length * percent_float)
                        bar = '▓' * filled + '░' * (bar_length - filled)

                        if int(percent) - last_percent >= 5 or percent == 100:
                            last_percent = int(percent)
                            try:
                                await progress_msg.edit(f"📥 Downloading:\n`[{bar}] {percent:.2f}%`")
                            except Exception as e:
                                print(f"[WARNING] Progress bar update failed: {e}")

        print(f"[LOG] Download complete: {temp_path}")

        # Download complete, now show "Sending to you..." loader
        sending_loader = ["⬆️ Sending to you.", "⬆️ Sending to you..", "⬆️ Sending to you..."]
        loader_index = 0
        sending = True

        async def sending_animation():
            nonlocal loader_index, sending
            while sending:
                try:
                    await progress_msg.edit(sending_loader[loader_index % len(sending_loader)])
                except Exception as e:
                    print(f"[WARNING] Sending loader update failed: {e}")
                loader_index += 1
                await asyncio.sleep(1)

        loader_task = asyncio.create_task(sending_animation())

        caption = (
            f"File Name: {info['name']}\n"
            f"File Size: {info['size_str']}\n"
            f"Link: {url}"
        )

        # Check if file is video (by extension)
        video_exts = [".mp4", ".mkv", ".webm", ".mov", ".avi"]
        is_video = any(info["name"].lower().endswith(ext) for ext in video_exts)

        if is_video:
            print(f"[LOG] Sending as video to user: {message.chat.id}")
            sent_msg = await client.send_video(
                chat_id=message.chat.id,
                video=temp_path,
                caption=caption,
                supports_streaming=True
            )
        else:
            print(f"[LOG] Sending as document to user: {message.chat.id}")
            sent_msg = await client.send_document(
                chat_id=message.chat.id,
                document=temp_path,
                caption=caption,
                file_name=info["name"],
                protect_content=True
            )

        sending = False
        await loader_task
        await progress_msg.edit("✅ Done! Video sent.")

        await message.reply("✅ File will be deleted from your chat after 12 hours.")
        print("[LOG] Waiting 12 hours before deleting the file from chat...")
        await asyncio.sleep(43200)
        try:
            await sent_msg.delete()
            print("[LOG] File deleted from chat.")
        except Exception as e:
            print(f"[WARNING] Failed to delete file from chat: {e}")

    except Exception as e:
        print(f"[ERROR] Upload failed: {e}")
        await message.reply(f"❌ Upload failed:\n`{e}`")

    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
            print(f"[LOG] Temp file deleted: {temp_path}")
