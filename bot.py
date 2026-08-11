import asyncio
import os
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import yt_dlp

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.types import (
    Message,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    TelegramObject,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from dotenv import load_dotenv

import db


# ============================================================
# Настройки
# ============================================================

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise ValueError("Переменная BOT_TOKEN не установлена")

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))


def is_admin(user_id: int) -> bool:
    return ADMIN_ID != 0 and user_id == ADMIN_ID


bot = Bot(token=TOKEN)
dp = Dispatcher()

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)


# ============================================================
# Трекинг пользователей (для админ-аналитики)
# ============================================================

class UserTrackingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:

        user = data.get("event_from_user")

        if user is not None:
            db.touch_user(user.id, user.username, user.first_name)

        return await handler(event, data)


dp.update.outer_middleware(UserTrackingMiddleware())


# ============================================================
# FSM состояния
# ============================================================

class DownloadState(StatesGroup):
    waiting_for_url = State()
    choosing_type = State()
    choosing_quality = State()


async def reset_state(state: FSMContext):
    """Сбрасывает FSM и возвращает готовность принять новую ссылку."""
    await state.clear()
    await state.set_state(DownloadState.waiting_for_url)


# ============================================================
# Определение платформы
# ============================================================

def get_platform(url: str):
    domain = urlparse(url).netloc.lower()

    if "youtube.com" in domain or "youtu.be" in domain:
        return "youtube"

    if "tiktok.com" in domain:
        return "tiktok"

    return None


# ============================================================
# Клавиатура выбора типа
# ============================================================

def download_type_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎥 Видео",
                    callback_data="type_video"
                ),
                InlineKeyboardButton(
                    text="🎵 MP3",
                    callback_data="type_audio"
                ),
            ]
        ]
    )


# ============================================================
# Получение информации о видео
# ============================================================

def get_video_info(url: str):
    options = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=False)


# ============================================================
# Клавиатура с качествами
# ============================================================

def quality_keyboard(formats):
    buttons = []

    for f in formats:
        format_id = f.get("format_id")
        resolution = f.get("resolution")
        fps = f.get("fps")
        filesize = f.get("filesize")

        if not resolution:
            continue

        text = resolution

        if fps:
            text += f" {fps}fps"

        if filesize:
            size_mb = filesize / 1024 / 1024
            text += f" — {size_mb:.1f} MB"

        buttons.append(
            [
                InlineKeyboardButton(
                    text=text,
                    callback_data=f"quality:{format_id}"
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="cancel"
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ============================================================
# /start
# ============================================================

@dp.message(CommandStart())
async def start_handler(message: Message, state: FSMContext):

    await state.clear()

    await state.set_state(DownloadState.waiting_for_url)

    await message.answer(
        "Привет! 👋\n\n"
        "Отправь мне ссылку на YouTube или TikTok."
    )


# ============================================================
# Админ: статистика
# ============================================================

@dp.message(Command("admin_stats"))
async def admin_stats_handler(message: Message):

    if not is_admin(message.from_user.id):
        return

    stats = db.get_stats()

    by_platform = "\n".join(
        f"  • {platform}: {count}"
        for platform, count in stats["by_platform"].items()
    ) or "  —"

    by_type = "\n".join(
        f"  • {media_type}: {count}"
        for media_type, count in stats["by_type"].items()
    ) or "  —"

    text = (
        "📊 Статистика бота\n\n"
        f"👥 Пользователей всего: {stats['total_users']}\n"
        f"🆕 Новых за 24ч: {stats['new_today']}\n"
        f"🆕 Новых за 7д: {stats['new_week']}\n"
        f"🟢 Активных за 24ч: {stats['active_today']}\n"
        f"🟢 Активных за 7д: {stats['active_week']}\n\n"
        f"⬇️ Скачиваний всего: {stats['total_downloads']}\n"
        f"⬇️ Скачиваний за 24ч: {stats['downloads_today']}\n\n"
        f"По платформам:\n{by_platform}\n\n"
        f"По типу:\n{by_type}"
    )

    await message.answer(text)


# ============================================================
# Админ: список пользователей
# ============================================================

@dp.message(Command("admin_users"))
async def admin_users_handler(message: Message):

    if not is_admin(message.from_user.id):
        return

    users = db.get_recent_users(limit=20)

    if not users:
        await message.answer("Пользователей пока нет.")
        return

    lines = ["👥 Последние пользователи (до 20, по активности):\n"]

    for u in users:
        name = f"@{u['username']}" if u["username"] else (u["first_name"] or "—")

        lines.append(
            f"• {name} (id {u['user_id']})\n"
            f"   первый визит: {u['first_seen'][:16]}\n"
            f"   последняя активность: {u['last_seen'][:16]}"
        )

    await message.answer("\n".join(lines))


# ============================================================
# Админ: топ пользователей по скачиваниям
# ============================================================

@dp.message(Command("admin_top"))
async def admin_top_handler(message: Message):

    if not is_admin(message.from_user.id):
        return

    users = db.get_top_users(limit=10)

    if not users:
        await message.answer("Скачиваний пока не было.")
        return

    lines = ["🏆 Топ пользователей по скачиваниям:\n"]

    for u in users:
        name = f"@{u['username']}" if u["username"] else (u["first_name"] or "—")
        lines.append(f"• {name} (id {u['user_id']}) — {u['downloads']}")

    await message.answer("\n".join(lines))


# ============================================================
# Админ: ссылки конкретного пользователя
# ============================================================

@dp.message(Command("admin_links"))
async def admin_links_handler(message: Message):

    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)

    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer(
            "Использование: /admin_links <user_id>\n\n"
            "Id пользователей можно посмотреть через /admin_users."
        )
        return

    user_id = int(parts[1].strip())

    downloads = db.get_user_downloads(user_id, limit=20)

    if not downloads:
        await message.answer("У этого пользователя пока нет скачиваний.")
        return

    lines = [f"🔗 Последние ссылки пользователя {user_id} (до 20):\n"]

    for d in downloads:
        lines.append(
            f"• [{d['platform']}/{d['media_type']}] "
            f"{d['url'] or '—'}\n   {d['created_at'][:16]}"
        )

    await message.answer("\n".join(lines))


# ============================================================
# Получение ссылки
# ============================================================

@dp.message(StateFilter(None, DownloadState.waiting_for_url))
async def url_handler(message: Message, state: FSMContext):

    if not message.text:
        await message.answer(
            "❌ Пожалуйста, отправь ссылку текстом."
        )
        return

    url = message.text.strip()

    platform = get_platform(url)

    if platform is None:
        await message.answer(
            "❌ Я не смог определить платформу.\n\n"
            "Отправь ссылку на YouTube или TikTok."
        )
        return

    # Сохраняем данные пользователя в FSM
    await state.update_data(
        url=url,
        platform=platform
    )

    await state.set_state(DownloadState.choosing_type)

    await message.answer(
        "✅ Ссылка распознана!\n\n"
        "Что ты хочешь скачать?",
        reply_markup=download_type_keyboard()
    )


# ============================================================
# Выбор "Видео"
# ============================================================

@dp.callback_query(
    DownloadState.choosing_type,
    F.data == "type_video"
)
async def video_type_handler(
    callback: CallbackQuery,
    state: FSMContext
):

    await callback.answer()

    data = await state.get_data()

    url = data["url"]
    platform = data["platform"]

    await callback.message.edit_text(
        "⏳ Получаю доступные качества..."
    )

    try:

        info = await asyncio.to_thread(
            get_video_info,
            url
        )

    except Exception as e:

        await callback.message.edit_text(
            f"❌ Не удалось получить информацию о видео.\n\n"
            f"{e}"
        )

        await reset_state(state)

        await callback.message.answer( "🔗 Попробуй отправить новую ссылку." )

        return

    formats = []

    for f in info["formats"]:

        # Только видео
        if f.get("vcodec") == "none":
            continue

        # Для YouTube нам нужны MP4
        if platform == "youtube":
            if f.get("ext") != "mp4":
                continue

        resolution = f.get("resolution")

        if not resolution:
            continue

        formats.append(f)

    # Убираем дубликаты по разрешению
    unique_formats = {}

    for f in formats:

        resolution = f.get("resolution")

        if resolution not in unique_formats:
            unique_formats[resolution] = f

    formats = list(unique_formats.values())

    if not formats:

        await callback.message.edit_text(
            "❌ Не удалось найти подходящие видеоформаты."
        )

        await reset_state(state)

        await callback.message.answer( "🔗 Жду новую ссылку." )
        
        return

    # Сортируем от маленького качества к большому
    formats.sort(
        key=lambda x: (
            x.get("height") or 0,
            x.get("fps") or 0
        )
    )

    await state.update_data(
        formats=[
            {
                "format_id": f["format_id"],
                "resolution": f.get("resolution"),
                "height": f.get("height") or 0,
                "fps": f.get("fps"),
                "filesize": f.get("filesize"),
            }
            for f in formats
        ]
    )

    await state.set_state(
        DownloadState.choosing_quality
    )

    await callback.message.edit_text(
        "🎥 Выбери качество:",
        reply_markup=quality_keyboard(formats)
    )


# ============================================================
# Выбор MP3
# ============================================================

@dp.callback_query(
    DownloadState.choosing_type,
    F.data == "type_audio"
)
async def audio_type_handler(
    callback: CallbackQuery,
    state: FSMContext
):

    await callback.answer()

    data = await state.get_data()

    url = data["url"]
    platform = data["platform"]

    await callback.message.edit_text(
        "⏳ Скачиваю аудио..."
    )

    try:

        filepath = await asyncio.to_thread(
            download_audio,
            url,
            callback.from_user.id
        )

    except Exception as e:

        await callback.message.edit_text(
            f"❌ Ошибка при скачивании:\n\n{e}"
        )

        await reset_state(state)

        await callback.message.answer( "🔗 Жду новую ссылку." )

        return

    db.log_download(callback.from_user.id, platform, "audio", url)

    await callback.message.edit_text(
        "✅ Аудио скачано!\n\n"
        "Отправляю файл..."
    )

    try:

        audio = FSInputFile(filepath)

        await callback.message.answer_document(
            audio
        )

    except Exception as e:

        await callback.message.answer(
            f"❌ Не удалось отправить файл:\n\n{e}"
        )

    await reset_state(state)

    await callback.message.answer( "🔗 Жду новую ссылку." )


# ============================================================
# Выбор качества
# ============================================================

@dp.callback_query(
    DownloadState.choosing_quality,
    F.data.startswith("quality:")
)
async def quality_handler(
    callback: CallbackQuery,
    state: FSMContext
):

    await callback.answer()

    format_id = callback.data.split(":", 1)[1]

    data = await state.get_data()

    url = data["url"]
    platform = data["platform"]

    await callback.message.edit_text(
        "⏳ Скачиваю видео...\n\n"
        "Это может занять некоторое время."
    )

    try:

        filepath = await asyncio.to_thread(
            download_video,
            url,
            format_id,
            platform,
            callback.from_user.id
        )

    except Exception as e:

        await callback.message.edit_text(
            f"❌ Ошибка при скачивании:\n\n{e}"
        )

        await reset_state(state)

        await callback.message.answer( "🔗 Жду новую ссылку." )

        return

    db.log_download(callback.from_user.id, platform, "video", url)

    await callback.message.edit_text(
        "✅ Видео скачано!\n\n"
        "Отправляю файл..."
    )

    try:

        video = FSInputFile(filepath)

        await callback.message.answer_document(
            video
        )

    except Exception as e:

        await callback.message.answer(
            f"❌ Не удалось отправить файл:\n\n{e}"
        )

    await reset_state(state)

    await callback.message.answer( "🔗 Жду новую ссылку." )


# ============================================================
# Отмена
# ============================================================

@dp.callback_query(F.data == "cancel")
async def cancel_handler(
    callback: CallbackQuery,
    state: FSMContext
):

    await callback.answer()

    await reset_state(state)

    await callback.message.edit_text(
        "❌ Скачивание отменено.\n\n"
        "Отправь новую ссылку."
    )


# ============================================================
# Скачивание видео
# ============================================================

def download_video(
    url: str,
    format_id: str,
    platform: str,
    user_id: int
):

    user_dir = DOWNLOAD_DIR / str(user_id)
    user_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    if platform == "youtube":

        options = {
            "format": (
                f"{format_id}+"
                "bestaudio[ext=m4a]/"
                "bestaudio"
            ),

            "outtmpl": str(
                user_dir / "%(id)s.%(ext)s"
            ),

            "noplaylist": True,

            "merge_output_format": "mp4",

            "quiet": True,
        }

    else:

        options = {
            "format": format_id,

            "outtmpl": str(
                user_dir / "%(id)s.%(ext)s"
            ),

            "noplaylist": True,

            "quiet": True,
        }

    with yt_dlp.YoutubeDL(options) as ydl:

        info = ydl.extract_info(
            url,
            download=True
        )

        video_id = info["id"]

    # Ищем скачанный файл
    files = list(user_dir.glob(f"{video_id}.*"))

    if not files:
        raise FileNotFoundError(
            "Скачанный файл не найден."
        )

    # Если несколько файлов, выбираем видео
    for file in files:

        if file.suffix.lower() in (
            ".mp4",
            ".webm",
            ".mkv",
            ".mov"
        ):
            return str(file)

    return str(files[0])


# ============================================================
# Скачивание аудио
# ============================================================

def download_audio(
    url: str,
    user_id: int
):

    user_dir = DOWNLOAD_DIR / str(user_id)

    user_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    options = {
        "format": "bestaudio",

        "outtmpl": str(
            user_dir / "%(id)s.%(ext)s"
        ),

        "noplaylist": True,

        "quiet": True,

        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }

    with yt_dlp.YoutubeDL(options) as ydl:

        info = ydl.extract_info(
            url,
            download=True
        )

        video_id = info["id"]

    mp3_file = user_dir / f"{video_id}.mp3"

    if not mp3_file.exists():

        raise FileNotFoundError(
            "MP3-файл не найден."
        )

    return str(mp3_file)


# ============================================================
# Запуск бота
# ============================================================

async def main():

    db.init_db()

    print("🤖 Бот запущен!")

    await bot.delete_webhook(drop_pending_updates=True)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
#проверка