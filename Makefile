VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: setup run update check clean

# Полная настройка на новом устройстве: venv + зависимости + проверки
setup:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@$(MAKE) --no-print-directory check

# Проверка окружения: .env с токеном и ffmpeg
check:
	@test -f .env && echo "OK: .env найден" || echo "ВНИМАНИЕ: создайте файл .env со строкой BOT_TOKEN=<токен от BotFather>"
	@command -v ffmpeg > /dev/null && echo "OK: ffmpeg установлен" || echo "ВНИМАНИЕ: ffmpeg не найден — установите его (apt install ffmpeg), без него не будет склейки видео/аудио и mp3"

# Запуск бота. Если в .env заданы BOT_API_LOCAL=true и TG_API_ID/TG_API_HASH
# (и в PATH есть бинарник telegram-bot-api) — перед ботом поднимается свой
# Local Bot API Server для отправки файлов больше 20 MB; при остановке бота
# (Ctrl+C) сервер гасится вместе с ним.
run:
	@set -a; \
	[ -f .env ] && . ./.env; \
	set +a; \
	if [ "$$BOT_API_LOCAL" = "true" ] && [ -n "$$TG_API_ID" ] && [ -n "$$TG_API_HASH" ] && command -v telegram-bot-api > /dev/null; then \
		PORT=$$(echo "$$BOT_API_SERVER" | grep -oE '[0-9]+$$'); \
		PORT=$${PORT:-8081}; \
		echo "Запускаю telegram-bot-api на порту $$PORT..."; \
		mkdir -p "$$HOME/.local/share/telegram-bot-api"; \
		telegram-bot-api --api-id="$$TG_API_ID" --api-hash="$$TG_API_HASH" --local --http-ip-address=127.0.0.1 --http-port="$$PORT" --dir="$$HOME/.local/share/telegram-bot-api" > telegram-bot-api.log 2>&1 & \
		trap "echo 'Останавливаю telegram-bot-api...'; kill $$! 2>/dev/null" EXIT; \
		sleep 1; \
	elif [ "$$BOT_API_LOCAL" = "true" ]; then \
		echo "ВНИМАНИЕ: BOT_API_LOCAL=true, но TG_API_ID/TG_API_HASH не заданы в .env или telegram-bot-api не найден в PATH — запускаю бота без него"; \
	fi; \
	$(PYTHON) bot.py

# Обновление зависимостей (в первую очередь yt-dlp — он быстро устаревает)
update:
	$(PIP) install --upgrade -r requirements.txt

# Удаление окружения
clean:
	rm -rf $(VENV)
