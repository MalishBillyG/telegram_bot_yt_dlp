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

# Запуск бота
run:
	$(PYTHON) bot.py

# Обновление зависимостей (в первую очередь yt-dlp — он быстро устаревает)
update:
	$(PIP) install --upgrade -r requirements.txt

# Удаление окружения
clean:
	rm -rf $(VENV)
