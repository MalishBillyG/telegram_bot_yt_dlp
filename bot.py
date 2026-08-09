import asyncio
import os


from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message


TOKEN = os.getenv("BOT_TOKEN")

dp = Dispatcher()


@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "Привет! 👋\n\n"
        "Отправь мне ссылку на YouTube или TikTok."
    )


async def main():
    bot = Bot(token=TOKEN)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())