import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from engine import ContentEngine, VALID_BUCKETS, VALID_STYLES, format_post, get_posting_bucket

load_dotenv()
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", level=logging.INFO)
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


def _state(context: ContextTypes.DEFAULT_TYPE, key: str, default):
    return context.application.bot_data.get(key, default)


def _set_state(context: ContextTypes.DEFAULT_TYPE, engine: ContentEngine, key: str, value: str | int | bool) -> None:
    context.application.bot_data[key] = value
    if isinstance(value, bool):
        v = "1" if value else "0"
    else:
        v = str(value)
    engine.set_runtime_config(key, v)


def _feedback_buttons(content_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("👍 7", callback_data=f"fb:{content_id}:7"), InlineKeyboardButton("🔥 9", callback_data=f"fb:{content_id}:9"), InlineKeyboardButton("🚀 10", callback_data=f"fb:{content_id}:10")]])


def _menu_markup(autopilot: bool, style: str, cooldown: int, monetization: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔍 Preview Best", callback_data="menu:preview"), InlineKeyboardButton("📤 Publish Best", callback_data="menu:publish")],
            [InlineKeyboardButton("📅 Day Plan", callback_data="menu:plan")],
            [InlineKeyboardButton("🏆 Top Утро", callback_data="menu:top:утро"), InlineKeyboardButton("🏆 Top День", callback_data="menu:top:день"), InlineKeyboardButton("🏆 Top Вечер", callback_data="menu:top:вечер")],
            [InlineKeyboardButton("🎛 Style Viral", callback_data="style:viral"), InlineKeyboardButton("🧾 Checklist", callback_data="style:checklist")],
            [InlineKeyboardButton("📚 Story", callback_data="style:story"), InlineKeyboardButton("⚡ Concise", callback_data="style:concise")],
            [InlineKeyboardButton(f"🤖 Autopilot: {'ON' if autopilot else 'OFF'}", callback_data="autopilot:toggle"), InlineKeyboardButton(f"💸 Monetize: {'ON' if monetization else 'OFF'}", callback_data="monetize:toggle")],
            [InlineKeyboardButton(f"🧠 Style: {style}", callback_data="noop"), InlineKeyboardButton(f"⏳ Cooldown: {cooldown}h", callback_data="noop")],
            [InlineKeyboardButton("📊 Stats", callback_data="menu:stats")],
        ]
    )


def _effective_style(context: ContextTypes.DEFAULT_TYPE, bucket: str, autopilot: bool) -> str:
    engine: ContentEngine = context.application.bot_data["engine"]
    if autopilot:
        return engine.recommend_best_style(bucket=bucket)
    return _state(context, "post_style", "viral")


def _build_monetization_block(context: ContextTypes.DEFAULT_TYPE, content_id: int, bucket: str, style: str) -> str:
    enabled = bool(_state(context, "monetization_enabled", False))
    offer_url = str(_state(context, "offer_url", "")).strip()
    offer_cta = str(_state(context, "offer_cta", "")).strip() or "Хочешь больше?"
    if not enabled or not offer_url:
        return ""

    utm = f"utm_source=telegram&utm_medium=channel&utm_campaign=content_{content_id}&utm_term={quote(bucket)}&utm_content={quote(style)}"
    joiner = "&" if "?" in offer_url else "?"
    tracked = f"{offer_url}{joiner}{utm}"
    return f"\n\n💸 <b>Монетизация:</b> {offer_cta}\n🔗 {tracked}"


async def _publish_ranked(context: ContextTypes.DEFAULT_TYPE, ranked, bucket: str | None, style: str) -> None:
    engine: ContentEngine = context.application.bot_data["engine"]
    cfg: BotConfig = context.application.bot_data["config"]
    effective_bucket = bucket or ranked.item.get("posting_time", "день")
    base_text = format_post(ranked.content_id, ranked.item, style=style)
    text = base_text + _build_monetization_block(context, ranked.content_id, effective_bucket, style)

    if cfg.dry_run:
        logger.info("DRY_RUN publish | bucket=%s style=%s score=%.2f", effective_bucket, style, ranked.score)
        return

    await context.bot.send_message(chat_id=cfg.channel_id, text=text, parse_mode="HTML", reply_markup=_feedback_buttons(ranked.content_id))
    engine.mark_posted(ranked.content_id, ranked.score, bucket=effective_bucket, style=style)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text("Бот активен ✅\n/menu — панель управления")


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: BotConfig = context.application.bot_data["config"]
    if not await _admin_guard(update, cfg):
        return
    if update.message:
        await update.message.reply_text(
            "Панель управления:",
            reply_markup=_menu_markup(
                bool(_state(context, "autopilot", True)),
                str(_state(context, "post_style", "viral")),
                int(_state(context, "cooldown_hours", cfg.cooldown_hours)),
                bool(_state(context, "monetization_enabled", False)),
            ),
        )


async def health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    engine: ContentEngine = context.application.bot_data["engine"]
    if update.message:
        await update.message.reply_text(
            f"ok\ncontents={len(engine.contents)}\nautopilot={_state(context, 'autopilot', True)}\n"
            f"style={_state(context, 'post_style', 'viral')}\ncooldown={_state(context, 'cooldown_hours', 24)}\n"
            f"monetization={_state(context, 'monetization_enabled', False)}"
        )


async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: BotConfig = context.application.bot_data["config"]
    if update.message:
        await update.message.reply_text(
            f"timezone={cfg.timezone}\nschedule={','.join(cfg.schedule_times)}\n"
            f"cooldown_hours={_state(context, 'cooldown_hours', cfg.cooldown_hours)}\n"
            f"style={_state(context, 'post_style', 'viral')}\nautopilot={_state(context, 'autopilot', True)}\n"
            f"monetization={_state(context, 'monetization_enabled', False)}"
        )


async def set_style(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: BotConfig = context.application.bot_data["config"]
    engine: ContentEngine = context.application.bot_data["engine"]
    if not await _admin_guard(update, cfg) or not update.message:
        return
    if len(context.args) != 1 or context.args[0] not in VALID_STYLES:
        await update.message.reply_text(f"Использование: /style <{'|'.join(sorted(VALID_STYLES))}>")
        return
    style = context.args[0]
    _set_state(context, engine, "post_style", style)
    await update.message.reply_text(f"Стиль установлен: {style}")


async def set_cooldown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: BotConfig = context.application.bot_data["config"]
    engine: ContentEngine = context.application.bot_data["engine"]
    if not await _admin_guard(update, cfg) or not update.message:
        return
    if len(context.args) != 1:
        await update.message.reply_text("Использование: /set_cooldown <hours>")
        return
    try:
        hours = int(context.args[0])
        if hours < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("cooldown должен быть целым числом >= 0")
        return
    _set_state(context, engine, "cooldown_hours", hours)
    await update.message.reply_text(f"Cooldown установлен: {hours}h")


async def autopilot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: BotConfig = context.application.bot_data["config"]
    engine: ContentEngine = context.application.bot_data["engine"]
    if not await _admin_guard(update, cfg) or not update.message:
        return
    if len(context.args) != 1 or context.args[0] not in {"on", "off"}:
        await update.message.reply_text("Использование: /autopilot <on|off>")
        return
    enabled = context.args[0] == "on"
    _set_state(context, engine, "autopilot", enabled)
    await update.message.reply_text(f"Autopilot {'включен' if enabled else 'выключен'}")


async def monetize_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: BotConfig = context.application.bot_data["config"]
    engine: ContentEngine = context.application.bot_data["engine"]
    if not await _admin_guard(update, cfg) or not update.message:
        return
    if len(context.args) != 1 or context.args[0] not in {"on", "off"}:
        await update.message.reply_text("Использование: /monetize <on|off>")
        return
    enabled = context.args[0] == "on"
    _set_state(context, engine, "monetization_enabled", enabled)
    await update.message.reply_text(f"Монетизация {'включена' if enabled else 'выключена'}")


async def set_offer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: BotConfig = context.application.bot_data["config"]
    engine: ContentEngine = context.application.bot_data["engine"]
    if not await _admin_guard(update, cfg) or not update.message:
        return
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /set_offer <url> <cta_text>")
        return
    url = context.args[0]
    cta = " ".join(context.args[1:])
    _set_state(context, engine, "offer_url", url)
    _set_state(context, engine, "offer_cta", cta)
    await update.message.reply_text("Оффер сохранен ✅")


async def publish_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    engine: ContentEngine = context.application.bot_data["engine"]
    cfg: BotConfig = context.application.bot_data["config"]
    if not await _admin_guard(update, cfg):
        return

    auto = bool(_state(context, "autopilot", True))
    cooldown = int(_state(context, "cooldown_hours", cfg.cooldown_hours))
    bucket = engine.recommend_best_bucket() if auto else None
    style = _effective_style(context, bucket or "день", auto)
    ranked = engine.pick_next(posting_time=bucket, cooldown_hours=cooldown)

    if cfg.dry_run and update.message:
        preview = format_post(ranked.content_id, ranked.item, style=style) + _build_monetization_block(context, ranked.content_id, bucket or ranked.item.get("posting_time", "день"), style)
        await update.message.reply_text(
            f"DRY_RUN\nautopilot={auto}\nbucket={bucket or 'auto'}\nstyle={style}\nscore={ranked.score:.2f}\n\n{preview}",
            parse_mode="HTML",
            reply_markup=_feedback_buttons(ranked.content_id),
        )
        return

    await _publish_ranked(context, ranked, bucket=bucket, style=style)
    if update.message:
        await update.message.reply_text("Опубликовано ✅")


async def publish_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    engine: ContentEngine = context.application.bot_data["engine"]
    cfg: BotConfig = context.application.bot_data["config"]
    if not await _admin_guard(update, cfg) or not update.message:
        return
    if len(context.args) != 1:
        await update.message.reply_text("Использование: /publish_id <content_id>")
        return
    try:
        cid = int(context.args[0])
        item = engine.get_content(cid)
    except Exception:
        await update.message.reply_text("Некорректный content_id")
        return

    style = str(_state(context, "post_style", "viral"))
    base = format_post(cid, item, style=style)
    text = base + _build_monetization_block(context, cid, item.get("posting_time", "день"), style)

    if cfg.dry_run:
        await update.message.reply_text(f"DRY_RUN\n\n{text}", parse_mode="HTML", reply_markup=_feedback_buttons(cid))
        return

    await context.bot.send_message(chat_id=cfg.channel_id, text=text, parse_mode="HTML", reply_markup=_feedback_buttons(cid))
    engine.mark_posted(cid, float(item["trend_score"]), bucket=item.get("posting_time"), style=style)
    await update.message.reply_text("Опубликовано ✅")


async def top_candidates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    engine: ContentEngine = context.application.bot_data["engine"]
    cfg: BotConfig = context.application.bot_data["config"]
    if not await _admin_guard(update, cfg) or not update.message:
        return
    bucket = context.args[0] if context.args else None
    if bucket and bucket not in VALID_BUCKETS:
        await update.message.reply_text("Неверный bucket. Используй: утро, день, вечер")
        return
    ranked = engine.rank_candidates(posting_time=bucket, top_n=5, cooldown_hours=int(_state(context, "cooldown_hours", cfg.cooldown_hours)))
    await update.message.reply_text("\n".join([f"Топ ({bucket or 'все'}):"] + [f"{i}. #{r.content_id} {r.item['title']} | {r.score:.2f}" for i, r in enumerate(ranked, 1)]))


async def queue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    engine: ContentEngine = context.application.bot_data["engine"]
    cfg: BotConfig = context.application.bot_data["config"]
    if not await _admin_guard(update, cfg) or not update.message:
        return
    n, bucket = 5, None
    for arg in context.args:
        if arg.isdigit():
            n = max(1, min(20, int(arg)))
        elif arg in VALID_BUCKETS:
            bucket = arg
    ranked = engine.rank_candidates(posting_time=bucket, top_n=n, cooldown_hours=int(_state(context, "cooldown_hours", cfg.cooldown_hours)))
    await update.message.reply_text("\n".join([f"Очередь ({bucket or 'all'}, n={n}):"] + [f"{i}. #{r.content_id} {r.item['title']} | {r.score:.2f}" for i, r in enumerate(ranked, 1)]))


async def plan_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    engine: ContentEngine = context.application.bot_data["engine"]
    cfg: BotConfig = context.application.bot_data["config"]
    if not await _admin_guard(update, cfg) or not update.message:
        return
    auto = bool(_state(context, "autopilot", True))
    cooldown = int(_state(context, "cooldown_hours", cfg.cooldown_hours))
    plan = engine.plan_for_buckets(["утро", "день", "вечер"], cooldown_hours=cooldown)
    lines = [f"Дневной план (autopilot={auto}):"]
    for b in ["утро", "день", "вечер"]:
        if b in plan:
            style = _effective_style(context, b, auto)
            lines.append(f"- {b}: #{plan[b].content_id} {plan[b].item['title']} | score={plan[b].score:.2f} | style={style}")
        else:
            lines.append(f"- {b}: нет кандидата")
    await update.message.reply_text("\n".join(lines))


async def feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    engine: ContentEngine = context.application.bot_data["engine"]
    if not update.message:
        return
    if len(context.args) != 2:
        await update.message.reply_text("Использование: /feedback <content_id> <1..10>")
        return
    try:
        engine.add_feedback(int(context.args[0]), float(context.args[1]))
    except Exception:
        await update.message.reply_text("Некорректные значения")
        return
    await update.message.reply_text("Оценка сохранена ✅")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    engine: ContentEngine = context.application.bot_data["engine"]
    if not update.message:
        return
    s = engine.stats()
    await update.message.reply_text(
        f"Posts={s['total_posts']}\nFeedback={s['total_feedback']}\nAvgFeedback={s['avg_feedback']}\nAvgPick={s['avg_pick_score']}\n"
        f"Best bucket={engine.recommend_best_bucket()}\nBest style={engine.recommend_best_style()}\nBest platform={engine.recommend_best_platform()}\n"
        f"Monetization={_state(context, 'monetization_enabled', False)}"
    )


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    engine: ContentEngine = context.application.bot_data["engine"]
    cfg: BotConfig = context.application.bot_data["config"]
    q = update.callback_query
    if not q:
        return
    await q.answer()
    data = q.data or ""

    if data == "noop":
        return

    if data.startswith("fb:"):
        _, cid, score = data.split(":", 2)
        try:
            engine.add_feedback(int(cid), float(score))
            await q.message.reply_text("Feedback сохранен ✅")
        except Exception:
            await q.message.reply_text("Ошибка сохранения feedback")
        return

    if not await _admin_guard(update, cfg):
        return

    if data == "autopilot:toggle":
        enabled = not bool(_state(context, "autopilot", True))
        _set_state(context, engine, "autopilot", enabled)
        await q.edit_message_text("Настройки обновлены", reply_markup=_menu_markup(enabled, str(_state(context, "post_style", "viral")), int(_state(context, "cooldown_hours", cfg.cooldown_hours)), bool(_state(context, "monetization_enabled", False))))
        return

    if data == "monetize:toggle":
        enabled = not bool(_state(context, "monetization_enabled", False))
        _set_state(context, engine, "monetization_enabled", enabled)
        await q.edit_message_text("Монетизация обновлена", reply_markup=_menu_markup(bool(_state(context, "autopilot", True)), str(_state(context, "post_style", "viral")), int(_state(context, "cooldown_hours", cfg.cooldown_hours)), enabled))
        return

    if data.startswith("style:"):
        style = data.split(":", 1)[1]
        if style in VALID_STYLES:
            _set_state(context, engine, "post_style", style)
            await q.edit_message_text("Стиль обновлен", reply_markup=_menu_markup(bool(_state(context, "autopilot", True)), style, int(_state(context, "cooldown_hours", cfg.cooldown_hours)), bool(_state(context, "monetization_enabled", False))))
        return

    if data == "menu:plan":
        plan = engine.plan_for_buckets(["утро", "день", "вечер"], cooldown_hours=int(_state(context, "cooldown_hours", cfg.cooldown_hours)))
        lines = ["Дневной план:"]
        for b in ["утро", "день", "вечер"]:
            if b in plan:
                lines.append(f"- {b}: #{plan[b].content_id} {plan[b].item['title']} | {plan[b].score:.2f}")
        await q.message.reply_text("\n".join(lines))
        return

    if data == "menu:preview":
        auto = bool(_state(context, "autopilot", True))
        bucket = engine.recommend_best_bucket() if auto else None
        style = _effective_style(context, bucket or "день", auto)
        ranked = engine.pick_next(posting_time=bucket, cooldown_hours=int(_state(context, "cooldown_hours", cfg.cooldown_hours)))
        text = format_post(ranked.content_id, ranked.item, style=style) + _build_monetization_block(context, ranked.content_id, bucket or ranked.item.get("posting_time", "день"), style)
        await q.message.reply_text(text, parse_mode="HTML", reply_markup=_feedback_buttons(ranked.content_id))
        return

    if data == "menu:publish":
        auto = bool(_state(context, "autopilot", True))
        bucket = engine.recommend_best_bucket() if auto else None
        style = _effective_style(context, bucket or "день", auto)
        ranked = engine.pick_next(posting_time=bucket, cooldown_hours=int(_state(context, "cooldown_hours", cfg.cooldown_hours)))
        if cfg.dry_run:
            text = format_post(ranked.content_id, ranked.item, style=style) + _build_monetization_block(context, ranked.content_id, bucket or ranked.item.get("posting_time", "день"), style)
            await q.message.reply_text("DRY_RUN\n\n" + text, parse_mode="HTML", reply_markup=_feedback_buttons(ranked.content_id))
        else:
            await _publish_ranked(context, ranked, bucket=bucket, style=style)
            await q.message.reply_text("Опубликовано ✅")
        return

    if data.startswith("menu:top:"):
        bucket = data.split(":", 2)[2]
        ranked = engine.rank_candidates(posting_time=bucket, top_n=5, cooldown_hours=int(_state(context, "cooldown_hours", cfg.cooldown_hours)))
        await q.message.reply_text("\n".join([f"Топ ({bucket}):"] + [f"{i}. #{r.content_id} {r.item['title']} | {r.score:.2f}" for i, r in enumerate(ranked, 1)]))
        return

    if data == "menu:stats":
        s = engine.stats()
        await q.message.reply_text(f"Posts={s['total_posts']} Feedback={s['total_feedback']} AvgPick={s['avg_pick_score']}")


async def scheduled_publish(context: ContextTypes.DEFAULT_TYPE) -> None:
    engine: ContentEngine = context.application.bot_data["engine"]
    cfg: BotConfig = context.application.bot_data["config"]
    auto = bool(_state(context, "autopilot", True))
    current_bucket = get_posting_bucket(datetime.now(ZoneInfo(cfg.timezone)))
    bucket = engine.recommend_best_bucket() if auto else current_bucket
    style = _effective_style(context, bucket, auto)
    ranked = engine.pick_next(posting_time=bucket, cooldown_hours=int(_state(context, "cooldown_hours", cfg.cooldown_hours)))

    if cfg.dry_run:
        logger.info("DRY_RUN scheduled | bucket=%s style=%s score=%.2f", bucket, style, ranked.score)
        return
    await _publish_ranked(context, ranked, bucket=bucket, style=style)


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
    ZoneInfo(timezone)
    schedule_times = [t.strip() for t in os.getenv("SCHEDULE_TIMES", "09:30,14:00,19:30").split(",") if t.strip()]
    if not schedule_times:
        raise ValueError("SCHEDULE_TIMES must contain at least one HH:MM value")
    for t in schedule_times:
        datetime.strptime(t, "%H:%M")

    dry_run = os.getenv("DRY_RUN", "true").lower() in {"1", "true", "yes", "on"}
    cooldown_hours = max(0, int(os.getenv("COOLDOWN_HOURS", "24")))

    raw_admin = os.getenv("ADMIN_IDS", "").strip()
    admin_ids = {int(x.strip()) for x in raw_admin.split(",") if x.strip()} if raw_admin else set()

    return BotConfig(token, channel_id, content_file, timezone, dry_run, schedule_times, cooldown_hours, admin_ids)


def schedule_jobs(app: Application) -> None:
    cfg: BotConfig = app.bot_data["config"]
    tz = ZoneInfo(cfg.timezone)
    for i, t in enumerate(cfg.schedule_times, 1):
        run_t = datetime.strptime(t, "%H:%M").time().replace(tzinfo=tz)
        app.job_queue.run_daily(scheduled_publish, time=run_t, name=f"daily_{i}_{t}")


def main() -> None:
    cfg = load_config()
    engine = ContentEngine(cfg.content_file)

    persisted_style = engine.get_runtime_config("post_style", os.getenv("POST_STYLE", "viral"))
    style = persisted_style if persisted_style in VALID_STYLES else "viral"
    autopilot = str(engine.get_runtime_config("autopilot", os.getenv("AUTOPILOT", "1"))).lower() in {"1", "true", "yes", "on"}
    cooldown = max(0, int(engine.get_runtime_config("cooldown_hours", str(cfg.cooldown_hours)) or cfg.cooldown_hours))
    monetization_enabled = str(engine.get_runtime_config("monetization_enabled", os.getenv("MONETIZATION_ENABLED", "0"))).lower() in {"1", "true", "yes", "on"}
    offer_url = engine.get_runtime_config("offer_url", os.getenv("OFFER_URL", "")) or ""
    offer_cta = engine.get_runtime_config("offer_cta", os.getenv("OFFER_CTA", "Полезные материалы и продукты")) or "Полезные материалы и продукты"

    app = Application.builder().token(cfg.token).build()
    app.bot_data.update(
        {
            "engine": engine,
            "config": cfg,
            "post_style": style,
            "autopilot": autopilot,
            "cooldown_hours": cooldown,
            "monetization_enabled": monetization_enabled,
            "offer_url": offer_url,
            "offer_cta": offer_cta,
        }
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("health", health))
    app.add_handler(CommandHandler("settings", settings))
    app.add_handler(CommandHandler("style", set_style))
    app.add_handler(CommandHandler("set_cooldown", set_cooldown))
    app.add_handler(CommandHandler("autopilot", autopilot_cmd))
    app.add_handler(CommandHandler("monetize", monetize_cmd))
    app.add_handler(CommandHandler("set_offer", set_offer))
    app.add_handler(CommandHandler("publish_now", publish_now))
    app.add_handler(CommandHandler("publish_id", publish_id))
    app.add_handler(CommandHandler("top", top_candidates))
    app.add_handler(CommandHandler("queue", queue))
    app.add_handler(CommandHandler("plan_day", plan_day))
    app.add_handler(CommandHandler("feedback", feedback))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_error_handler(on_error)

    schedule_jobs(app)
    logger.info(
        "Bot started | dry_run=%s | autopilot=%s | style=%s | cooldown=%s | monetization=%s",
        cfg.dry_run,
        autopilot,
        style,
        cooldown,
        monetization_enabled,
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
