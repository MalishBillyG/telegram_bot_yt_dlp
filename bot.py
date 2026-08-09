import asyncio
import os

from urllib.parse import urlparse

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message

from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise ValueError("Переменная BOT_TOKEN не установлена")


dp = Dispatcher()


def get_platform(url):
    domain = urlparse(url).netloc.lower()

    if "youtube.com" in domain or "youtu.be" in domain:
        return "youtube"

    if "tiktok.com" in domain:
        return "tiktok"

    return None


@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "Привет! 👋\n\n"
        "Отправь мне ссылку на YouTube или TikTok."
    )


@dp.message()
async def message_handler(message: Message):
    url = message.text

    platform = get_platform(url)

    if platform == "youtube":
        await message.answer("✅ Это YouTube")

    elif platform == "tiktok":
        await message.answer("✅ Это TikTok")

    else:
        await message.answer(
            "❌ Я не смог определить платформу.\n\n"
            "Отправь ссылку на YouTube или TikTok."
        )


async def main():
    bot = Bot(token=TOKEN)

    print("🤖 Бот запущен!")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())