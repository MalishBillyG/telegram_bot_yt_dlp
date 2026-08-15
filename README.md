# Telegram Bot — YouTube/TikTok/Instagram Downloader

Telegram-бот на `aiogram` для скачивания видео (YouTube, TikTok, Instagram) и
извлечения аудио (MP3) через `yt-dlp`.

## Возможности

- Принимает ссылку на YouTube, TikTok или Instagram
- Даёт выбор: скачать видео (с выбором качества) или извлечь MP3
- Скачивает файл и отправляет его пользователю в Telegram

> Instagram у `yt-dlp` работает менее стабильно, чем YouTube/TikTok —
> без авторизации (cookies) публичные посты/reels обычно скачиваются, но
> Instagram может отдавать ошибку логина/rate-limit чаще, чем другие
> платформы. Опционально можно настроить cookies залогиненного аккаунта —
> см. раздел [Cookies для Instagram](#cookies-для-instagram).

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
BOT_API_SERVER=<опционально, см. ниже>
BOT_API_LOCAL=<опционально, см. ниже>
INSTAGRAM_COOKIES_FILE=<опционально, см. ниже>
```

`ADMIN_ID` нужен только для доступа к админ-командам (см. ниже). Узнать
свой telegram id можно, например, у [@userinfobot](https://t.me/userinfobot).

`BOT_API_SERVER`/`BOT_API_LOCAL` нужны только если хотите отправлять файлы
больше 20 MB — см. раздел
[Отправка больших файлов](#отправка-больших-файлов-telegram-bot-api-server).

`INSTAGRAM_COOKIES_FILE` нужен только чтобы Instagram работал стабильнее —
см. раздел [Cookies для Instagram](#cookies-для-instagram).

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
  разбивка по платформам (YouTube/TikTok/Instagram) и по типу (видео/аудио)
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

## Отправка больших файлов (telegram-bot-api server)

Обычный облачный Bot API (`api.telegram.org`) не даёт ботам отправлять файлы
больше **~20–50 MB**. Чтобы снять это ограничение, нужно поднять свой
[Local Bot API Server](https://github.com/tdlib/telegram-bot-api) (официальный,
от Telegram) и указать боту его адрес через `BOT_API_SERVER`.

Ниже — вариант **бот и сервер на одной машине** (именно так и настроено).
В этом случае имеет смысл использовать флаг `--local`: тогда `bot.py` не
заливает файл на сервер по HTTP, а просто передаёт ему абсолютный путь на
диске — сервер читает файл сам. Это быстрее и практически без ограничения
на размер (заливки по сети нет вообще, только чтение с диска).

### 1. Получите `api_id` и `api_hash`

Зайдите на https://my.telegram.org → **API development tools** → создайте
приложение (любое название/платформа сгодится). Получите `api_id` (число) и
`api_hash` (строка). Это бесплатно и не связано с ботом — это creds
приложения, от имени которого локальный сервер общается с Telegram.

### 2. Запустите telegram-bot-api с `--local`

Без Docker (проще всего, раз всё на одной машине — не нужно думать про
совпадение путей между хостом и контейнером). Соберите бинарник из
исходников по инструкции в
[официальном репозитории](https://github.com/tdlib/telegram-bot-api#building)
(C++, нужен cmake/gperf/openssl) и запустите:

```bash
telegram-bot-api \
  --api-id=<api_id> \
  --api-hash=<api_hash> \
  --local \
  --http-ip-address=127.0.0.1 \
  --http-port=8081
```

`--http-ip-address=127.0.0.1` — сервер слушает только локально, снаружи
машины порт `8081` вообще не виден (доп. firewall не нужен, но лишним не
будет).

Если предпочитаете Docker — используйте образ
[aiogram/telegram-bot-api](https://hub.docker.com/r/aiogram/telegram-bot-api)
(сверьтесь со страницей образа за актуальными именами переменных), но
**обязательно** смонтируйте папку `downloads/` проекта в контейнер по тому
же абсолютному пути, что и на хосте — иначе сервер не найдёт файл по пути,
который передаст ему бот:

```bash
docker run -d \
  --name telegram-bot-api \
  --restart unless-stopped \
  -p 127.0.0.1:8081:8081 \
  -e TELEGRAM_API_ID=<api_id> \
  -e TELEGRAM_API_HASH=<api_hash> \
  -v /opt/telegram-bot-api-data:/var/lib/telegram-bot-api \
  -v "$(pwd)/downloads:$(pwd)/downloads" \
  aiogram/telegram-bot-api:latest --local
```

Проверьте, что сервер поднялся:

```bash
curl http://127.0.0.1:8081/
# должен ответить что-то вроде "Error: 404 Not Found" — это нормально,
# значит сервер жив и просто не понял путь без токена
```

### 3. Укажите боту адрес сервера и включите local-режим

В `.env` на машине с `bot.py`:

```
BOT_API_SERVER=http://127.0.0.1:8081
BOT_API_LOCAL=true
```

Если `BOT_API_SERVER` не задан — бот как и раньше работает через обычный
`api.telegram.org` с лимитом 20 MB. С `BOT_API_SERVER` без `--local`/
`BOT_API_LOCAL` — лимит поднимается до 2000 MB (файл всё ещё заливается по
HTTP, просто на свой сервер вместо облачного). С `BOT_API_LOCAL=true`
(и соответствующим `--local` на сервере) заливки по HTTP нет вообще — бот
передаёт серверу путь к файлу на диске (`make_input_file()` в `bot.py`
сам решает, что отправлять: `FSInputFile` или строку с абсолютным путём).

Перезапустите бота (`make run`).

### Если решите разнести бота и сервер по разным машинам

`--local` и передача файла по локальному пути требуют общей файловой
системы — для разных машин так не сработает. В этом случае уберите
`--local` (сервер всё равно даст лимит в 2000 MB через обычную HTTP-заливку)
и **обязательно** закройте `8081` от внешнего доступа — HTTP без шифрования
и без аутентификации, доступ к порту = доступ к боту от вашего имени.
Проще всего — SSH-туннель (`ssh -N -L 8081:localhost:8081 user@сервер`) или
VPN (WireGuard/Tailscale) между машинами, и `BOT_API_SERVER=http://localhost:8081`
на стороне бота (через туннель).

## Cookies для Instagram

Без авторизации Instagram чаще отдаёт `yt-dlp` ошибки логина/rate-limit, чем
YouTube или TikTok. Чтобы бот обращался к Instagram от имени залогиненного
аккаунта, можно передать `yt-dlp` cookies этого аккаунта.

**Учтите: файл с cookies — по сути сохранённая сессия входа, чувствительные
данные уровня пароля.** Всё, что скачивает бот через Instagram, будет
выглядеть как активность этого аккаунта — используйте отдельный аккаунт,
не основной личный, и не публикуйте/не коммитьте файл с cookies в git.

### 1. Экспортируйте cookies.txt

Залогиньтесь в Instagram в браузере на нужном аккаунте и экспортируйте
cookies в формате Netscape, например расширением
[Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
для Chrome/Edge (или аналог для Firefox) — откройте instagram.com, нажмите
на иконку расширения, экспортируйте cookies текущей вкладки в файл.

Альтернатива без расширений — сам `yt-dlp` умеет вытащить cookies прямо из
установленного браузера:

```bash
.venv/bin/yt-dlp --cookies-from-browser chrome --cookies instagram_cookies.txt --skip-download "https://www.instagram.com/"
```

(вместо `chrome` — `firefox`, `edge` и т.п., смотря где залогинены)

### 2. Положите файл рядом с ботом и укажите путь

```bash
mv instagram_cookies.txt /root/claude_projects/telegram_bot_yt_dlp/
```

В `.env`:

```
INSTAGRAM_COOKIES_FILE=/root/claude_projects/telegram_bot_yt_dlp/instagram_cookies.txt
```

В `.gitignore` добавлено правило `*cookies*.txt` — файл под именем из
примера выше коммититься не будет. Если назовёте файл иначе, проверьте,
что он всё равно попадает под игнор (или добавьте своё правило) — коммитить
его в git нельзя.

Если переменная не задана или файл по указанному пути не существует — бот
просто обращается к Instagram анонимно, как и раньше, без ошибок.

### 3. Обновляйте cookies по мере протухания

Сессия рано или поздно истекает (Instagram может разлогинить). Если после
рабочей связки внезапно снова посыпались ошибки логина — просто повторите
шаг 1 и перезапишите файл, перезапускать бота не обязательно (`cookiefile`
читается при каждом скачивании заново).
