import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from engine import ContentEngine, VALID_BUCKETS, VALID_STYLES, format_post, get_posting_bucket

load_dotenv()
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("telegabot")


@dataclass
class BotConfig:
    token: str
    channel_id: str
    content_file: Path
    timezone: str
    dry_run: bool
    schedule_times: list[str]
    cooldown_hours: int
    admin_ids: set[int]


def _current_style(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.application.bot_data.get("post_style", "viral")


def _autopilot_enabled(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return context.application.bot_data.get("autopilot", True)


def _is_admin(update: Update, cfg: BotConfig) -> bool:
    if not cfg.admin_ids:
        return True
    user = update.effective_user
    return bool(user and user.id in cfg.admin_ids)


async def _admin_guard(update: Update, cfg: BotConfig) -> bool:
    if _is_admin(update, cfg):
        return True
    if update.message:
        await update.message.reply_text("⛔ Команда доступна только администраторам.")
    elif update.callback_query:
        await update.callback_query.answer("Только для админов", show_alert=True)
    return False


def _feedback_buttons(content_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("👍 7", callback_data=f"fb:{content_id}:7"),
            InlineKeyboardButton("🔥 9", callback_data=f"fb:{content_id}:9"),
            InlineKeyboardButton("🚀 10", callback_data=f"fb:{content_id}:10"),
        ]]
    )


def _menu_markup(autopilot: bool, style: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔍 Preview Best", callback_data="menu:preview"),
                InlineKeyboardButton("📤 Publish Best", callback_data="menu:publish"),
            ],
            [
                InlineKeyboardButton("🏆 Top Утро", callback_data="menu:top:утро"),
                InlineKeyboardButton("🏆 Top День", callback_data="menu:top:день"),
                InlineKeyboardButton("🏆 Top Вечер", callback_data="menu:top:вечер"),
            ],
            [
                InlineKeyboardButton("🎛 Style Viral", callback_data="style:viral"),
                InlineKeyboardButton("🧾 Style Checklist", callback_data="style:checklist"),
            ],
            [
                InlineKeyboardButton("📚 Style Story", callback_data="style:story"),
                InlineKeyboardButton("⚡ Style Concise", callback_data="style:concise"),
            ],
            [
                InlineKeyboardButton(
                    f"🤖 Autopilot: {'ON' if autopilot else 'OFF'}",
                    callback_data="autopilot:toggle",
                ),
                InlineKeyboardButton(f"🧠 Current: {style}", callback_data="noop"),
            ],
            [InlineKeyboardButton("📊 Stats", callback_data="menu:stats")],
        ]
    )


def _effective_style(context: ContextTypes.DEFAULT_TYPE, bucket: str, autopilot: bool) -> str:
    engine: ContentEngine = context.application.bot_data["engine"]
    if autopilot:
        return engine.recommend_best_style(bucket=bucket)
    return _current_style(context)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "Бот активен ✅\n"
            "Открой удобный интерфейс кнопок: /menu\n"
            "Команды: /publish_now, /publish_id <id>, /top [утро|день|вечер], /feedback <id> <1..10>, /stats, /style <style>, /autopilot, /health, /settings",
        )


async def health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: BotConfig = context.application.bot_data["config"]
    engine: ContentEngine = context.application.bot_data["engine"]
    if update.message:
        await update.message.reply_text(
            f"ok\n"
            f"contents={len(engine.contents)}\n"
            f"dry_run={cfg.dry_run}\n"
            f"style={_current_style(context)}\n"
            f"autopilot={_autopilot_enabled(context)}\n"
            f"cooldown_hours={cfg.cooldown_hours}"
        )


async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: BotConfig = context.application.bot_data["config"]
    if update.message:
        await update.message.reply_text(
            f"timezone={cfg.timezone}\n"
            f"schedule={','.join(cfg.schedule_times)}\n"
            f"cooldown_hours={cfg.cooldown_hours}\n"
            f"post_style={_current_style(context)}\n"
            f"autopilot={_autopilot_enabled(context)}\n"
            f"admin_ids={'set' if cfg.admin_ids else 'not_set'}"
        )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: BotConfig = context.application.bot_data["config"]
    if not await _admin_guard(update, cfg):
        return
    if update.message:
        await update.message.reply_text(
            "Панель управления контентом:",
            reply_markup=_menu_markup(_autopilot_enabled(context), _current_style(context)),
        )


async def set_style(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: BotConfig = context.application.bot_data["config"]
    engine: ContentEngine = context.application.bot_data["engine"]
    if not await _admin_guard(update, cfg):
        return
    if not update.message:
        return
    if len(context.args) != 1 or context.args[0] not in VALID_STYLES:
        await update.message.reply_text(f"Использование: /style <{'|'.join(sorted(VALID_STYLES))}>")
        return
    style = context.args[0]
    context.application.bot_data["post_style"] = style
    engine.set_runtime_config("post_style", style)
    await update.message.reply_text(f"Стиль поста установлен: {style}")


async def autopilot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: BotConfig = context.application.bot_data["config"]
    engine: ContentEngine = context.application.bot_data["engine"]
    if not await _admin_guard(update, cfg):
        return
    if not update.message:
        return

    if len(context.args) != 1 or context.args[0] not in {"on", "off"}:
        await update.message.reply_text("Использование: /autopilot <on|off>")
        return

    enabled = context.args[0] == "on"
    context.application.bot_data["autopilot"] = enabled
    engine.set_runtime_config("autopilot", "1" if enabled else "0")
    await update.message.reply_text(f"Autopilot {'включен' if enabled else 'выключен'}")


async def publish_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    engine: ContentEngine = context.application.bot_data["engine"]
    cfg: BotConfig = context.application.bot_data["config"]
    if not await _admin_guard(update, cfg):
        return

    autopilot_on = _autopilot_enabled(context)
    bucket = engine.recommend_best_bucket() if autopilot_on else None
    style = _effective_style(context, bucket or "день", autopilot_on)

    ranked = engine.pick_next(posting_time=bucket, cooldown_hours=cfg.cooldown_hours)
    text = format_post(ranked.content_id, ranked.item, style=style)

    if cfg.dry_run:
        if update.message:
            await update.message.reply_text(
                f"DRY_RUN\nautopilot={autopilot_on}\nbucket={bucket or 'auto'}\nstyle={style}\nscore={ranked.score:.2f}\n\n{text}",
                parse_mode="HTML",
                reply_markup=_feedback_buttons(ranked.content_id),
            )
        return

    await context.bot.send_message(
        chat_id=cfg.channel_id,
        text=text,
        parse_mode="HTML",
        reply_markup=_feedback_buttons(ranked.content_id),
    )
    engine.mark_posted(ranked.content_id, ranked.score, bucket=bucket, style=style)
    if update.message:
        await update.message.reply_text(
            f"Опубликовано в канал: {ranked.item['title']} (score={ranked.score:.2f}, style={style}, bucket={bucket or 'auto'})"
        )


async def publish_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    engine: ContentEngine = context.application.bot_data["engine"]
    cfg: BotConfig = context.application.bot_data["config"]
    if not await _admin_guard(update, cfg):
        return
    style = _current_style(context)
    if not update.message:
        return
    if len(context.args) != 1:
        await update.message.reply_text("Использование: /publish_id <content_id>")
        return
    try:
        content_id = int(context.args[0])
        item = engine.get_content(content_id)
    except Exception:
        await update.message.reply_text("Некорректный content_id")
        return

    text = format_post(content_id, item, style=style)
    if cfg.dry_run:
        await update.message.reply_text(
            f"DRY_RUN\nstyle={style}\n\n{text}",
            parse_mode="HTML",
            reply_markup=_feedback_buttons(content_id),
        )
        return

    await context.bot.send_message(
        chat_id=cfg.channel_id,
        text=text,
        parse_mode="HTML",
        reply_markup=_feedback_buttons(content_id),
    )
    engine.mark_posted(content_id, float(item["trend_score"]), bucket=item.get("posting_time"), style=style)
    await update.message.reply_text(f"Принудительно опубликовано: #{content_id} {item['title']}")


async def top_candidates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    engine: ContentEngine = context.application.bot_data["engine"]
    cfg: BotConfig = context.application.bot_data["config"]
    if not await _admin_guard(update, cfg):
        return
    if not update.message:
        return

    bucket = context.args[0] if context.args else None
    if bucket and bucket not in VALID_BUCKETS:
        await update.message.reply_text("Неверный bucket. Используй: утро, день, вечер")
        return

    ranked = engine.rank_candidates(posting_time=bucket, top_n=5, cooldown_hours=cfg.cooldown_hours)
    lines = [f"Топ кандидаты ({bucket or 'все'}):"]
    for idx, r in enumerate(ranked, start=1):
        lines.append(f"{idx}. #{r.content_id} {r.item['title']} | score={r.score:.2f}")
    await update.message.reply_text("\n".join(lines))


async def feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    engine: ContentEngine = context.application.bot_data["engine"]
    if not update.message:
        return
    if len(context.args) != 2:
        await update.message.reply_text("Использование: /feedback <content_id> <1..10>")
        return

    try:
        content_id = int(context.args[0])
        value = float(context.args[1])
        engine.add_feedback(content_id, value)
    except Exception:
        await update.message.reply_text("Некорректные значения. Пример: /feedback 5 8.5")
        return

    await update.message.reply_text(f"Оценка сохранена: content_id={content_id}, score={value}")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    engine: ContentEngine = context.application.bot_data["engine"]
    if not update.message:
        return

    s = engine.stats()
    top_lines = []
    for cid, avg_s, cnt in s["top"]:
        title = engine.contents[cid]["title"]
        top_lines.append(f"- {cid}: {title} | avg={avg_s:.2f} ({cnt})")
    top_text = "\n".join(top_lines) if top_lines else "Пока нет обратной связи"

    bucket_lines = []
    for bucket, cnt, avg_s in s["by_bucket"]:
        bucket_lines.append(f"- {bucket or 'n/a'}: {cnt} постов, avg_score={float(avg_s or 0):.2f}")
    bucket_text = "\n".join(bucket_lines) if bucket_lines else "Нет данных"

    style_lines = []
    for style, cnt, avg_s in s["by_style"]:
        style_lines.append(f"- {style or 'n/a'}: {cnt} постов, avg_score={float(avg_s or 0):.2f}")
    style_text = "\n".join(style_lines) if style_lines else "Нет данных"

    await update.message.reply_text(
        f"Постов: {s['total_posts']}\n"
        f"Feedback: {s['total_feedback']}\n"
        f"Средний score feedback: {s['avg_feedback']}\n"
        f"Средний score выбора: {s['avg_pick_score']}\n"
        f"Лучший bucket: {engine.recommend_best_bucket()}\n"
        f"Лучший style: {engine.recommend_best_style()}\n\n"
        f"Топ идеи:\n{top_text}\n\n"
        f"Bucket статистика:\n{bucket_text}\n\n"
        f"Style статистика:\n{style_text}"
    )


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    engine: ContentEngine = context.application.bot_data["engine"]
    cfg: BotConfig = context.application.bot_data["config"]
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""
    autopilot_on = _autopilot_enabled(context)

    if data == "noop":
        return

    if data.startswith("fb:"):
        _, raw_id, raw_score = data.split(":", 2)
        try:
            engine.add_feedback(int(raw_id), float(raw_score))
            await query.message.reply_text(f"Feedback сохранен: content_id={raw_id}, score={raw_score}")
        except Exception:
            await query.message.reply_text("Не удалось сохранить feedback")
        return

    if not await _admin_guard(update, cfg):
        return

    if data == "autopilot:toggle":
        enabled = not _autopilot_enabled(context)
        context.application.bot_data["autopilot"] = enabled
        engine.set_runtime_config("autopilot", "1" if enabled else "0")
        await query.edit_message_text(
            f"Autopilot {'ON' if enabled else 'OFF'}",
            reply_markup=_menu_markup(enabled, _current_style(context)),
        )
        return

    if data.startswith("style:"):
        new_style = data.split(":", 1)[1]
        if new_style in VALID_STYLES:
            context.application.bot_data["post_style"] = new_style
            engine.set_runtime_config("post_style", new_style)
            await query.edit_message_text(
                f"Стиль изменен: {new_style}",
                reply_markup=_menu_markup(_autopilot_enabled(context), new_style),
            )
        return

    if data == "menu:preview":
        bucket = engine.recommend_best_bucket() if autopilot_on else "день"
        style = _effective_style(context, bucket, autopilot_on)
        ranked = engine.pick_next(posting_time=bucket if autopilot_on else None, cooldown_hours=cfg.cooldown_hours)
        text = format_post(ranked.content_id, ranked.item, style=style)
        await query.message.reply_text(
            f"Preview | autopilot={autopilot_on} | bucket={bucket} | style={style} | score={ranked.score:.2f}\n\n{text}",
            parse_mode="HTML",
            reply_markup=_feedback_buttons(ranked.content_id),
        )
        return

    if data == "menu:publish":
        bucket = engine.recommend_best_bucket() if autopilot_on else None
        style = _effective_style(context, bucket or "день", autopilot_on)
        ranked = engine.pick_next(posting_time=bucket, cooldown_hours=cfg.cooldown_hours)
        text = format_post(ranked.content_id, ranked.item, style=style)
        if cfg.dry_run:
            await query.message.reply_text(
                f"DRY_RUN publish | autopilot={autopilot_on} | bucket={bucket or 'auto'} | style={style} | score={ranked.score:.2f}\n\n{text}",
                parse_mode="HTML",
                reply_markup=_feedback_buttons(ranked.content_id),
            )
        else:
            await context.bot.send_message(
                chat_id=cfg.channel_id,
                text=text,
                parse_mode="HTML",
                reply_markup=_feedback_buttons(ranked.content_id),
            )
            engine.mark_posted(ranked.content_id, ranked.score, bucket=bucket, style=style)
            await query.message.reply_text("Опубликовано в канал ✅")
        return

    if data.startswith("menu:top:"):
        bucket = data.split(":", 2)[2]
        ranked = engine.rank_candidates(posting_time=bucket, top_n=5, cooldown_hours=cfg.cooldown_hours)
        lines = [f"Топ кандидаты ({bucket}):"]
        for idx, r in enumerate(ranked, start=1):
            lines.append(f"{idx}. #{r.content_id} {r.item['title']} | score={r.score:.2f}")
        await query.message.reply_text("\n".join(lines))
        return

    if data == "menu:stats":
        s = engine.stats()
        await query.message.reply_text(
            f"Posts={s['total_posts']} | Feedback={s['total_feedback']} | AvgPick={s['avg_pick_score']}\n"
            f"Best bucket: {engine.recommend_best_bucket()} | Best style: {engine.recommend_best_style()}"
        )


async def scheduled_publish(context: ContextTypes.DEFAULT_TYPE) -> None:
    engine: ContentEngine = context.application.bot_data["engine"]
    cfg: BotConfig = context.application.bot_data["config"]
    autopilot_on = _autopilot_enabled(context)
    now = datetime.now(ZoneInfo(cfg.timezone))

    bucket = get_posting_bucket(now)
    if autopilot_on:
        bucket = engine.recommend_best_bucket()

    style = _effective_style(context, bucket, autopilot_on)
    ranked = engine.pick_next(posting_time=bucket, cooldown_hours=cfg.cooldown_hours)
    text = format_post(ranked.content_id, ranked.item, style=style)

    if cfg.dry_run:
        logger.info(
            "DRY_RUN scheduled | bucket=%s | score=%.2f | style=%s | autopilot=%s | title=%s",
            bucket,
            ranked.score,
            style,
            autopilot_on,
            ranked.item["title"],
        )
        return

    await context.bot.send_message(
        chat_id=cfg.channel_id,
        text=text,
        parse_mode="HTML",
        reply_markup=_feedback_buttons(ranked.content_id),
    )
    engine.mark_posted(ranked.content_id, ranked.score, bucket=bucket, style=style)
    logger.info(
        "Published scheduled post id=%s title=%s score=%.2f style=%s autopilot=%s",
        ranked.content_id,
        ranked.item["title"],
        ranked.score,
        style,
        autopilot_on,
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error: %s", context.error)


def load_config() -> BotConfig:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    channel_id = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
    content_file = Path(os.getenv("CONTENT_FILE", "content_ideas_2026_ru.json"))

    if not content_file.exists():
        raise FileNotFoundError(f"CONTENT_FILE not found: {content_file}")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is required")
    if not channel_id:
        raise ValueError("TELEGRAM_CHANNEL_ID is required")

    timezone = os.getenv("POST_TIMEZONE", "Europe/Moscow")
    try:
        ZoneInfo(timezone)
    except Exception as exc:
        raise ValueError(f"Invalid POST_TIMEZONE: {timezone}") from exc

    raw_schedule = os.getenv("SCHEDULE_TIMES", "09:30,14:00,19:30")
    schedule_times = [t.strip() for t in raw_schedule.split(",") if t.strip()]
    if not schedule_times:
        raise ValueError("SCHEDULE_TIMES must contain at least one HH:MM value")

    for t in schedule_times:
        try:
            datetime.strptime(t, "%H:%M")
        except ValueError as exc:
            raise ValueError(f"Invalid time in SCHEDULE_TIMES: {t}") from exc

    dry_run = os.getenv("DRY_RUN", "true").lower() in {"1", "true", "yes", "on"}
    cooldown_hours = int(os.getenv("COOLDOWN_HOURS", "24"))
    if cooldown_hours < 0:
        raise ValueError("COOLDOWN_HOURS must be >= 0")

    raw_admin = os.getenv("ADMIN_IDS", "").strip()
    admin_ids: set[int] = set()
    if raw_admin:
        try:
            admin_ids = {int(x.strip()) for x in raw_admin.split(",") if x.strip()}
        except ValueError as exc:
            raise ValueError("ADMIN_IDS must be comma-separated integers") from exc

    return BotConfig(
        token=token,
        channel_id=channel_id,
        content_file=content_file,
        timezone=timezone,
        dry_run=dry_run,
        schedule_times=schedule_times,
        cooldown_hours=cooldown_hours,
        admin_ids=admin_ids,
    )


def schedule_jobs(app: Application) -> None:
    cfg: BotConfig = app.bot_data["config"]
    jq = app.job_queue
    tz = ZoneInfo(cfg.timezone)

    for i, time_str in enumerate(cfg.schedule_times, start=1):
        run_time = datetime.strptime(time_str, "%H:%M").time().replace(tzinfo=tz)
        jq.run_daily(scheduled_publish, time=run_time, name=f"daily_{i}_{time_str}")


def main() -> None:
    cfg = load_config()
    engine = ContentEngine(cfg.content_file)

    persisted_style = engine.get_runtime_config("post_style", os.getenv("POST_STYLE", "viral"))
    post_style = persisted_style if persisted_style in VALID_STYLES else "viral"

    persisted_autopilot = engine.get_runtime_config("autopilot", os.getenv("AUTOPILOT", "1"))
    autopilot_enabled = str(persisted_autopilot).lower() in {"1", "true", "yes", "on"}

    app = Application.builder().token(cfg.token).build()
    app.bot_data["engine"] = engine
    app.bot_data["config"] = cfg
    app.bot_data["post_style"] = post_style
    app.bot_data["autopilot"] = autopilot_enabled

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("health", health))
    app.add_handler(CommandHandler("settings", settings))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("style", set_style))
    app.add_handler(CommandHandler("autopilot", autopilot))
    app.add_handler(CommandHandler("publish_now", publish_now))
    app.add_handler(CommandHandler("publish_id", publish_id))
    app.add_handler(CommandHandler("top", top_candidates))
    app.add_handler(CommandHandler("feedback", feedback))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_error_handler(on_error)

    schedule_jobs(app)
    logger.info(
        "Bot started | dry_run=%s | channel=%s | timezone=%s | schedule=%s | style=%s | autopilot=%s | cooldown=%s | admins=%s",
        cfg.dry_run,
        cfg.channel_id,
        cfg.timezone,
        ",".join(cfg.schedule_times),
        app.bot_data["post_style"],
        app.bot_data["autopilot"],
        cfg.cooldown_hours,
        len(cfg.admin_ids),
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
