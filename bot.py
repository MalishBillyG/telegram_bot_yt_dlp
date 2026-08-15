import asyncio
import os
import time
import traceback
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import yt_dlp

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    ErrorEvent,
    Message,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    MenuButtonCommands,
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

# Путь к cookies.txt залогиненного Instagram-аккаунта (формат Netscape,
# экспортируется расширением вроде "Get cookies.txt"). Опционально — без
# него Instagram работает анонимно и чаще ловит login/rate-limit ошибки.
INSTAGRAM_COOKIES_FILE = os.getenv("INSTAGRAM_COOKIES_FILE", "").strip()

# Адрес своего Bot API сервера (telegram-bot-api), напр. http://localhost:8081
# Если не задан — используется обычный облачный api.telegram.org с лимитом
# отправки файла в 20 MB.
BOT_API_SERVER = os.getenv("BOT_API_SERVER", "").strip()

# True, если telegram-bot-api запущен с флагом --local (бот и сервер должны
# быть на одной машине с общей файловой системой). В этом режиме файлы
# передаются серверу локальным путём напрямую с диска, без HTTP-заливки —
# и без ограничения на размер.
BOT_API_LOCAL = os.getenv("BOT_API_LOCAL", "").strip().lower() in ("1", "true", "yes")


def is_admin(user_id: int) -> bool:
    return ADMIN_ID != 0 and user_id == ADMIN_ID


if BOT_API_SERVER:
    session = AiohttpSession(
        api=TelegramAPIServer.from_base(
            BOT_API_SERVER,
            is_local=BOT_API_LOCAL,
        )
    )
    bot = Bot(token=TOKEN, session=session)

    if BOT_API_LOCAL:
        # Локальный путь на диске сервер читает сам — практических
        # ограничений на размер нет (кроме места на диске).
        MAX_FILE_SIZE = 4000 * 1024 * 1024
    else:
        # Свой Bot API сервер без --local всё равно поднимает лимит
        # отправки файла ботом до 2000 MB.
        MAX_FILE_SIZE = 2000 * 1024 * 1024
else:
    bot = Bot(token=TOKEN)
    # Лимит облачного api.telegram.org на отправку файла ботом
    MAX_FILE_SIZE = 20 * 1024 * 1024

dp = Dispatcher()

# Абсолютный путь важен для --local режима: telegram-bot-api — отдельный
# процесс и может быть запущен из другого рабочего каталога, относительный
# путь у него означал бы другое место на диске.
DOWNLOAD_DIR = Path(__file__).resolve().parent / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)


def make_input_file(path: str):
    """FSInputFile для обычной загрузки или "сырой" путь для --local режима
    (сервер сам читает файл с диска, без пересылки по HTTP)."""

    if BOT_API_LOCAL:
        return str(Path(path).resolve())

    return FSInputFile(path)

GENERIC_ERROR_TEXT = (
    "❌ Что-то пошло не так. Мы уже разбираемся.\n\n"
    "Попробуй ещё раз чуть позже."
)


# ============================================================
# Уведомление админа об ошибках
# ============================================================

async def notify_admin(text: str):
    """Отправляет текст админу, не роняя бота, если это не удалось."""

    if ADMIN_ID == 0:
        return

    if len(text) > 4000:
        text = text[:2000] + "\n...\n" + text[-1900:]

    try:
        await bot.send_message(ADMIN_ID, text)
    except Exception:
        pass


async def report_error(context: str, exc: Exception, **extra):
    """Логирует ошибку в консоль и шлёт подробности админу в личку."""

    details = "\n".join(f"{k}: {v}" for k, v in extra.items())
    tb = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )

    print(f"[ERROR] {context}\n{details}\n{tb}")

    await notify_admin(
        f"⚠️ Ошибка: {context}\n{details}\n\n{tb}"
    )


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

    if "instagram.com" in domain or "instagr.am" in domain:
        return "instagram"

    return None


def normalize_url(url: str, platform: str) -> str:
    """Убирает трекинг query-параметры (?_r=...&_t=...) из полных
    tiktok.com-ссылок. По наблюдениям из
    https://github.com/yt-dlp/yt-dlp/issues/17332 такие ссылки чаще ловят
    "Unable to extract universal data for rehydration" — "чистый" URL без
    хвоста стабильнее парсится. Короткие vt.tiktok.com/vm.tiktok.com-ссылки
    не трогаем, у них нет такого хвоста."""

    if platform != "tiktok":
        return url

    parsed = urlparse(url)

    if "vt.tiktok.com" in parsed.netloc or "vm.tiktok.com" in parsed.netloc:
        return url

    if not parsed.query:
        return url

    return parsed._replace(query="", fragment="").geturl()


# ============================================================
# Cookies для yt-dlp (сейчас только Instagram)
# ============================================================

def cookies_options(platform: str) -> dict:
    if platform != "instagram" or not INSTAGRAM_COOKIES_FILE:
        return {}

    if not os.path.isfile(INSTAGRAM_COOKIES_FILE):
        return {}

    return {"cookiefile": INSTAGRAM_COOKIES_FILE}


# ============================================================
# Retry для нестабильной TikTok-ошибки экстракции
# ============================================================

# TikTok иногда отдаёт страницу, которую yt-dlp не может распарсить —
# ошибка "Unable to extract universal data for rehydration". Это открытый
# баг на стороне TikTok/yt-dlp без фикса (issue #17332), но по наблюдениям
# из треда повторная попытка через пару секунд часто помогает.
TIKTOK_RETRYABLE_ERROR = "universal data for rehydration"
TIKTOK_RETRY_ATTEMPTS = 2
TIKTOK_RETRY_DELAY = 3.0


def extract_with_retry(ydl: "yt_dlp.YoutubeDL", url: str, platform: str, download: bool):
    attempt = 0

    while True:
        try:
            return ydl.extract_info(url, download=download)
        except Exception as exc:
            attempt += 1

            is_retryable = (
                platform == "tiktok"
                and TIKTOK_RETRYABLE_ERROR in str(exc).lower()
            )

            if not is_retryable or attempt > TIKTOK_RETRY_ATTEMPTS:
                raise

            time.sleep(TIKTOK_RETRY_DELAY)


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

def get_video_info(url: str, platform: str):
    options = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        **cookies_options(platform),
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        return extract_with_retry(ydl, url, platform, download=False)


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
        "Отправь мне ссылку на YouTube, TikTok или Instagram."
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
            "Отправь ссылку на YouTube, TikTok или Instagram."
        )
        return

    url = normalize_url(url, platform)

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
            url,
            platform
        )

    except Exception as e:

        await report_error(
            "get_video_info",
            e,
            user_id=callback.from_user.id,
            url=url,
        )

        await callback.message.edit_text(
            "❌ Не удалось получить информацию о видео.\n\n"
            "Попробуй другую ссылку или повтори позже."
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
            platform,
            callback.from_user.id
        )

    except Exception as e:

        await report_error(
            "download_audio",
            e,
            user_id=callback.from_user.id,
            url=url,
        )

        await callback.message.edit_text(GENERIC_ERROR_TEXT)

        await reset_state(state)

        await callback.message.answer( "🔗 Жду новую ссылку." )

        return

    db.log_download(callback.from_user.id, platform, "audio", url)

    file_size = os.path.getsize(filepath)

    if file_size > MAX_FILE_SIZE:

        await callback.message.edit_text(
            "⚠️ Файл слишком большой для отправки в Telegram "
            f"({file_size / 1024 / 1024:.1f} MB, лимит "
            f"{MAX_FILE_SIZE / 1024 / 1024:.0f} MB).\n\n"
            "К сожалению, отправить его не получится."
        )

        await reset_state(state)

        await callback.message.answer( "🔗 Жду новую ссылку." )

        return

    await callback.message.edit_text(
        "✅ Аудио скачано!\n\n"
        "Отправляю файл..."
    )

    try:

        audio = make_input_file(filepath)

        await callback.message.answer_document(
            audio
        )

    except Exception as e:

        await report_error(
            "send_audio",
            e,
            user_id=callback.from_user.id,
            url=url,
        )

        await callback.message.answer(GENERIC_ERROR_TEXT)

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

        await report_error(
            "download_video",
            e,
            user_id=callback.from_user.id,
            url=url,
            format_id=format_id,
        )

        await callback.message.edit_text(GENERIC_ERROR_TEXT)

        await reset_state(state)

        await callback.message.answer( "🔗 Жду новую ссылку." )

        return

    db.log_download(callback.from_user.id, platform, "video", url)

    file_size = os.path.getsize(filepath)

    if file_size > MAX_FILE_SIZE:

        await callback.message.edit_text(
            "⚠️ Файл слишком большой для отправки в Telegram "
            f"({file_size / 1024 / 1024:.1f} MB, лимит "
            f"{MAX_FILE_SIZE / 1024 / 1024:.0f} MB).\n\n"
            "Попробуй выбрать более низкое качество."
        )

        await reset_state(state)

        await callback.message.answer( "🔗 Жду новую ссылку." )

        return

    await callback.message.edit_text(
        "✅ Видео скачано!\n\n"
        "Отправляю файл..."
    )

    try:

        video = make_input_file(filepath)

        await callback.message.answer_document(
            video
        )

    except Exception as e:

        await report_error(
            "send_video",
            e,
            user_id=callback.from_user.id,
            url=url,
        )

        await callback.message.answer(GENERIC_ERROR_TEXT)

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
# Глобальный обработчик необработанных ошибок
# ============================================================

@dp.errors()
async def global_error_handler(event: ErrorEvent):

    update = event.update

    chat_id = None

    if update.message:
        chat_id = update.message.chat.id
    elif update.callback_query and update.callback_query.message:
        chat_id = update.callback_query.message.chat.id

    await report_error(
        "unhandled",
        event.exception,
        update_id=update.update_id,
        chat_id=chat_id,
    )

    if chat_id is not None:
        try:
            await bot.send_message(chat_id, GENERIC_ERROR_TEXT)
        except Exception:
            pass

    return True


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

            **cookies_options(platform),
        }

    else:

        options = {
            "format": format_id,

            "outtmpl": str(
                user_dir / "%(id)s.%(ext)s"
            ),

            "noplaylist": True,

            "quiet": True,

            **cookies_options(platform),
        }

    with yt_dlp.YoutubeDL(options) as ydl:

        info = extract_with_retry(ydl, url, platform, download=True)

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
    platform: str,
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

        **cookies_options(platform),
    }

    with yt_dlp.YoutubeDL(options) as ydl:

        info = extract_with_retry(ydl, url, platform, download=True)

        video_id = info["id"]

    mp3_file = user_dir / f"{video_id}.mp3"

    if not mp3_file.exists():

        raise FileNotFoundError(
            "MP3-файл не найден."
        )

    return str(mp3_file)


# ============================================================
# Команды бота (кнопка "Меню")
# ============================================================

async def setup_commands():

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Начать / перезапустить бота"),
        ],
        scope=BotCommandScopeDefault(),
    )

    if ADMIN_ID != 0:

        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Начать / перезапустить бота"),
                BotCommand(command="admin_stats", description="Статистика бота"),
                BotCommand(command="admin_users", description="Последние пользователи"),
                BotCommand(command="admin_top", description="Топ по скачиваниям"),
                BotCommand(command="admin_links", description="Ссылки пользователя <user_id>"),
            ],
            scope=BotCommandScopeChat(chat_id=ADMIN_ID),
        )

    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


# ============================================================
# Запуск бота
# ============================================================

async def main():

    db.init_db()

    print("🤖 Бот запущен!")

    await bot.delete_webhook(drop_pending_updates=True)

    await setup_commands()

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
#проверка