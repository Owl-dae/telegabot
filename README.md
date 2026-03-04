# TelegaBot — автопостинг + монетизация в Telegram

Бот рассчитан на рост аудитории и системную монетизацию: автопилот по контенту, удобный интерфейс, anti-repeat и встроенные monetization-блоки с UTM.

Посты автоматически усиливаются engagement-призывом (вопрос/реакция/комментарий), что повышает вовлечение.

## Что нового
- **Монетизация в постах**:
  - `/monetize <on|off>` — включение/выключение monetization-блока,
  - `/set_offer <url> <cta>` — оффер для вставки в каждый пост,
  - автодобавление UTM-меток (`content_id`, bucket, style) к ссылке.
- **Авто-оптимизация**:
  - autopilot выбирает лучший bucket + style,
  - учитывается cooldown и feedback-loop.
- **Управление и UX**:
  - `/menu` с inline-кнопками,
  - `/queue`, `/plan_day`, `/set_cooldown`.

## Команды
- `/start`, `/health`, `/settings`, `/menu`
- `/style <viral|concise|story|checklist>`
- `/autopilot <on|off>`
- `/set_cooldown <hours>`
- `/monetize <on|off>`
- `/set_offer <url> <cta_text>`
- `/publish_now`, `/publish_id <content_id>`
- `/top [утро|день|вечер]`
- `/queue [n] [утро|день|вечер]`
- `/plan_day`
- `/feedback <content_id> <1..10>`
- `/stats`
- `/optimize`
- `/trending [n] [утро|день|вечер]`
- `/help`

## Конфиг (`.env`)
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHANNEL_ID`
- `CONTENT_FILE`
- `POST_TIMEZONE`
- `SCHEDULE_TIMES`
- `POST_STYLE`
- `AUTOPILOT`
- `COOLDOWN_HOURS`
- `ADMIN_IDS`
- `MONETIZATION_ENABLED`
- `OFFER_URL`
- `OFFER_CTA`
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
