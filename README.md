# TelegaBot — максимально автоматизированный автопостинг в Telegram

Бот для публикации контента в канал с упором на: авто-режим, удобный интерфейс, минимальное ручное участие и постоянная оптимизация по метрикам.

## Что нового в этой версии
- **Autopilot mode**:
  - автоматически выбирает лучший `bucket` (`утро/день/вечер`) по историческому `score`;
  - автоматически выбирает лучший `style` (`viral/concise/story/checklist`) по истории публикаций.
- **Персистентные runtime-настройки**:
  - стиль и autopilot сохраняются в SQLite (`runtime_config`) и переживают рестарты.
- **Админ-безопасность**:
  - чувствительные действия ограничиваются `ADMIN_IDS`.
- **Анти-повторы**:
  - `COOLDOWN_HOURS` предотвращает частые повторные публикации одних и тех же идей.
- **Удобный UI**:
  - `/menu` + inline-кнопки для preview/publish/top/style/autopilot/stats.

## Архитектура
- `engine.py`:
  - валидация датасета,
  - ranking и выбор кандидатов,
  - аналитика по bucket/style,
  - runtime key-value config,
  - генерация поста по стилям.
- `bot.py`:
  - Telegram handlers,
  - защита админ-доступом,
  - scheduled posting,
  - callback UI.

## Команды
- `/start`
- `/health`
- `/settings`
- `/menu`
- `/style <viral|concise|story|checklist>`
- `/autopilot <on|off>`
- `/publish_now`
- `/publish_id <content_id>`
- `/top [утро|день|вечер]`
- `/feedback <content_id> <1..10>`
- `/stats`

## Конфиг (`.env`)
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHANNEL_ID`
- `CONTENT_FILE`
- `POST_TIMEZONE`
- `SCHEDULE_TIMES`
- `POST_STYLE`
- `AUTOPILOT` (`1/0`)
- `COOLDOWN_HOURS`
- `ADMIN_IDS` (например `12345,67890`)
- `DRY_RUN`

## Быстрый старт
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

## Тесты
```bash
python -m unittest tests/test_engine.py
```
