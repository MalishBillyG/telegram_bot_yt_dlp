# Telegram Bot — YouTube/TikTok Downloader

Telegram-бот на `aiogram` для скачивания видео (YouTube, TikTok) и извлечения
аудио (MP3) через `yt-dlp`.

## Возможности

- Принимает ссылку на YouTube или TikTok
- Даёт выбор: скачать видео (с выбором качества) или извлечь MP3
- Скачивает файл и отправляет его пользователю в Telegram

## Требования

- Python 3.10+
- `ffmpeg` (нужен для склейки видео/аудио дорожек и конвертации в MP3)
- Токен бота от [@BotFather](https://t.me/BotFather)

## Установка

```bash
make setup   # создаёт venv, ставит зависимости, проверяет .env и ffmpeg
```

Создайте файл `.env` в корне проекта:

```
BOT_TOKEN=<токен от BotFather>
ADMIN_ID=<ваш telegram id, опционально>
```

`ADMIN_ID` нужен только для доступа к админ-командам (см. ниже). Узнать
свой telegram id можно, например, у [@userinfobot](https://t.me/userinfobot).

## Запуск

```bash
make run
```

## Прочие команды Makefile

- `make check` — проверить наличие `.env` и `ffmpeg`
- `make update` — обновить зависимости (в первую очередь `yt-dlp`, он быстро устаревает)
- `make clean` — удалить venv

## Админ-команды

Доступны только пользователю с id, указанным в `ADMIN_ID`. Для остальных
эти команды никак не отвечают (бот молчит, будто их не существует).

- `/admin_stats` — общая аналитика: кол-во пользователей (всего/новых за
  24ч и 7д/активных за 24ч и 7д), кол-во скачиваний всего и за 24ч,
  разбивка по платформам (YouTube/TikTok) и по типу (видео/аудио)
- `/admin_users` — последние 20 пользователей по активности (id, username,
  дата первого визита и последней активности)
- `/admin_top` — топ-10 пользователей по количеству скачиваний
- `/admin_links <user_id>` — последние 20 ссылок, которые скачивал конкретный
  пользователь (id можно взять из `/admin_users`)

## Прямой доступ к базе (bot.db)

Через `sqlite3` CLI (`apt install sqlite3`, если не установлен):

```bash
sqlite3 bot.db
.tables
.schema users
.schema downloads
SELECT * FROM users WHERE user_id = 123456789;
SELECT * FROM downloads WHERE user_id = 123456789;
```

Либо без установки CLI — через встроенный в Python модуль `sqlite3`:

```bash
.venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('bot.db')
conn.row_factory = sqlite3.Row
for row in conn.execute('SELECT * FROM users'):
    print(dict(row))
"
```

## Структура

- `bot.py` — весь код бота (хендлеры, FSM-состояния, скачивание через yt-dlp,
  админ-команды)
- `db.py` — слой хранения (SQLite): пользователи и события скачиваний для
  админ-аналитики
- `bot.db` — файл базы SQLite, создаётся автоматически при первом запуске
  (в `.gitignore`)
- `downloads/` — временные скачанные файлы, разложенные по `user_id` (в `.gitignore`)
- `requirements.txt` — зависимости
- `Makefile` — управление окружением

## Как это работает (FSM)

1. `waiting_for_url` — ждём ссылку, определяем платформу по домену
2. `choosing_type` — пользователь выбирает "Видео" или "MP3"
3. `choosing_quality` — (только для видео) выбор из доступных разрешений
4. Скачивание выполняется в отдельном потоке (`asyncio.to_thread`), чтобы не блокировать бота
5. После отправки файла состояние сбрасывается (`reset_state`) и бот снова ждёт ссылку
