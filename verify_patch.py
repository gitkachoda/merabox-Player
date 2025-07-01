import aiohttp
import secrets
from datetime import datetime, timedelta
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram.types import Message
from config import DATABASE
import os

# Load verification settings from environment
IS_VERIFY = os.environ.get("IS_VERIFY", "False").lower() in ("true", "1", "yes")
SHORTLINK_URL = os.environ.get("SHORTLINK_URL", "linkshortify.com")
SHORTLINK_API = os.environ.get("SHORTLINK_API", "353689935e1e4ac6c70ba88c7e6e71dc6fe1e8c0")
HOW_TO_VERIFY = os.environ.get('HOW_TO_VERIFY', "https://t.me/mntgxo/22")

# MongoDB setup
mongo_client = AsyncIOMotorClient(DATABASE.URI)
mdb = mongo_client[DATABASE.NAME]
users_col = mdb["verifyusers"]
tokens_col = mdb["verifytokens"]

# Shorten a URL using shortlink service
async def short_link(url: str) -> str:
    try:
        async with aiohttp.ClientSession() as session:
            params = {"api": SHORTLINK_API, "url": url}
            async with session.get(f"https://{SHORTLINK_URL}/api", params=params) as resp:
                data = await resp.json()
                return data.get("shortenedUrl", url)
    except Exception as e:
        print(f"[Shortlink Error] {e}")
        return url

# Generate a secure token and store it
async def create_verification_token(user_id: int) -> str:
    token = secrets.token_urlsafe(16)
    await tokens_col.delete_many({"user_id": user_id})
    await tokens_col.insert_one({
        "user_id": user_id,
        "token": token,
        "used": False,
        "created_at": datetime.utcnow(),
        "expires_at": datetime.utcnow() + timedelta(minutes=30)
    })
    return token

# Build short verification link
async def build_verification_link(bot_username: str, user_id: int) -> str:
    token = await create_verification_token(user_id)
    full_link = f"https://t.me/{bot_username}?start=verify_{token}"
    return await short_link(full_link)

# Set user as verified
async def set_verified(user_id: int):
    expires_at = datetime.utcnow() + timedelta(hours=12)
    await users_col.update_one(
        {"_id": user_id},
        {"$set": {"is_verified": True, "expires_at": expires_at}},
        upsert=True
    )

# Check if user is verified and not expired
async def is_verified(user_id: int) -> bool:
    user = await users_col.find_one({"_id": user_id})
    if not user or not user.get("is_verified"):
        return False
    if datetime.utcnow() > user.get("expires_at", datetime.utcnow()):
        await users_col.update_one(
            {"_id": user_id},
            {"$set": {"is_verified": False}, "$unset": {"expires_at": ""}}
        )
        return False
    return True

# Validate token and mark user as verified
async def validate_token_and_verify(user_id: int, token: str) -> bool:
    record = await tokens_col.find_one({"token": token})
    if not record or record["used"] or record["user_id"] != user_id or datetime.utcnow() > record["expires_at"]:
        return False
    await tokens_col.update_one({"_id": record["_id"]}, {"$set": {"used": True}})
    await set_verified(user_id)
    return True

# Check and redirect to verification link if needed
async def check_and_redirect_verification(bot, message: Message):
    user_id = message.from_user.id
    if IS_VERIFY and not await is_verified(user_id):
        verify_url = await build_verification_link(bot.me.username, user_id)
        await message.reply_text(
            f"🔒 You must verify before using this bot:\n👉 [Click to Verify]({verify_url})\n⏳ Link valid for 24 hours.",
            disable_web_page_preview=True
        )
        return False
    return True
