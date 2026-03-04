import html
import json
import random
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

REQUIRED_FIELDS = {
    "title",
    "description",
    "hashtags",
    "call_to_action",
    "content_type",
    "script",
    "tone",
    "difficulty",
    "estimated_time",
    "trend_score",
    "source",
    "engagement_tip",
    "format",
    "visual_cues",
    "audio_cues",
    "variation",
    "content_goal",
    "posting_time",
    "platform_recommendation",
}

VALID_BUCKETS = {"утро", "день", "вечер"}
VALID_STYLES = {"viral", "concise", "story", "checklist"}


@dataclass
class RankedContent:
    content_id: int
    item: Dict[str, Any]
    score: float


class ContentEngine:
    def __init__(self, content_file: Path, db_path: Path = Path("bot_state.db")) -> None:
        self.content_file = Path(content_file)
        self.db_path = Path(db_path)
        self.contents = self._load_content()
        self._ensure_db()
        self._snapshot_cache_ttl_s = 20.0
        self._snapshot_cached_at = 0.0
        self._snapshot_cached_value = None
        self._snapshot_cache_key = None

    def _load_content(self) -> List[Dict[str, Any]]:
        raw = json.loads(self.content_file.read_text(encoding="utf-8"))
        if not isinstance(raw, list) or not raw:
            raise ValueError("CONTENT_FILE must contain a non-empty JSON array")

        for idx, item in enumerate(raw):
            missing = REQUIRED_FIELDS - item.keys()
            if missing:
                raise ValueError(f"Content #{idx} missing fields: {sorted(missing)}")
            if not isinstance(item["hashtags"], list) or not item["hashtags"]:
                raise ValueError(f"Content #{idx} has invalid hashtags")
            if not isinstance(item["variation"], list) or len(item["variation"]) < 2:
                raise ValueError(f"Content #{idx} must have at least 2 variations")
            if not isinstance(item["trend_score"], (int, float)):
                raise ValueError(f"Content #{idx} trend_score must be numeric")
            if not 1 <= float(item["trend_score"]) <= 10:
                raise ValueError(f"Content #{idx} trend_score must be between 1 and 10")
            if item.get("posting_time") not in VALID_BUCKETS:
                raise ValueError(f"Content #{idx} posting_time must be one of {sorted(VALID_BUCKETS)}")
        return raw

    def _ensure_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    posted_at TEXT NOT NULL,
                    score REAL DEFAULT NULL,
                    bucket TEXT DEFAULT NULL,
                    platform TEXT DEFAULT NULL,
                    style TEXT DEFAULT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content_id INTEGER NOT NULL,
                    value REAL NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(posts)").fetchall()}
            if "bucket" not in columns:
                conn.execute("ALTER TABLE posts ADD COLUMN bucket TEXT DEFAULT NULL")
            if "platform" not in columns:
                conn.execute("ALTER TABLE posts ADD COLUMN platform TEXT DEFAULT NULL")
            if "style" not in columns:
                conn.execute("ALTER TABLE posts ADD COLUMN style TEXT DEFAULT NULL")

            conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_content_id ON posts(content_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_posted_at ON posts(posted_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_bucket ON posts(bucket)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_style ON posts(style)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_content_id ON feedback(content_id)")
            conn.commit()

    def _invalidate_snapshot_cache(self) -> None:
        self._snapshot_cached_at = 0.0
        self._snapshot_cached_value = None
        self._snapshot_cache_key = None

    def get_runtime_config(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT value FROM runtime_config WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_runtime_config(self, key: str, value: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO runtime_config(key, value, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=datetime('now')
                """,
                (key, value),
            )
            conn.commit()

    def _metrics_snapshot(
        self,
        recency_hours: int = 72,
        cooldown_hours: int = 24,
    ) -> tuple[dict[int, float], dict[int, int], set[int], set[int]]:
        now_mono = time.monotonic()
        cache_key = (recency_hours, cooldown_hours)
        if (
            self._snapshot_cached_value
            and self._snapshot_cache_key == cache_key
            and (now_mono - self._snapshot_cached_at) <= self._snapshot_cache_ttl_s
        ):
            return self._snapshot_cached_value

        with sqlite3.connect(self.db_path) as conn:
            feedback_rows = conn.execute("SELECT content_id, AVG(value) FROM feedback GROUP BY content_id").fetchall()
            post_rows = conn.execute("SELECT content_id, COUNT(*) FROM posts GROUP BY content_id").fetchall()
            recent_rows = conn.execute(
                "SELECT DISTINCT content_id FROM posts WHERE posted_at >= datetime('now', ?)",
                (f"-{recency_hours} hours",),
            ).fetchall()
            cooldown_rows = conn.execute(
                "SELECT DISTINCT content_id FROM posts WHERE posted_at >= datetime('now', ?)",
                (f"-{max(0, cooldown_hours)} hours",),
            ).fetchall()

        feedback_avg = {cid: float(avg or 0) for cid, avg in feedback_rows}
        posts_count = {cid: int(cnt or 0) for cid, cnt in post_rows}
        recent_ids = {cid for (cid,) in recent_rows}
        cooldown_ids = {cid for (cid,) in cooldown_rows}
        value = (feedback_avg, posts_count, recent_ids, cooldown_ids)
        self._snapshot_cached_value = value
        self._snapshot_cached_at = now_mono
        self._snapshot_cache_key = cache_key
        return value

    def _seasonal_bonus(self, item: Dict[str, Any], now: Optional[datetime] = None) -> float:
        now = now or datetime.now()
        month = now.month
        text = f"{item.get('title','')} {item.get('description','')} {' '.join(item.get('hashtags', []))}".lower()

        season_map = {
            12: ["новый год", "зима", "итоги", "подар"],
            1: ["новый год", "январ", "резолюц", "привыч"],
            2: ["феврал", "челлендж", "привыч"],
            3: ["весн", "reset", "перезапуск"],
            4: ["весн", "детокс", "обновл"],
            5: ["май", "экзамен", "сесс"],
            6: ["лет", "каникул", "outdoor"],
            7: ["лет", "отпуск", "жара"],
            8: ["back to", "подготовк", "учеб"],
            9: ["школ", "учеб", "сентябр"],
            10: ["осен", "фокус", "продуктив"],
            11: ["black friday", "распрод", "итоги"],
        }
        keywords = season_map.get(month, [])
        hits = sum(1 for k in keywords if k in text)
        return min(1.2, hits * 0.4)

    def audit_content(self) -> Dict[str, Any]:
        issues: List[str] = []
        seen_titles: set[str] = set()

        for idx, item in enumerate(self.contents):
            title = str(item.get("title", "")).strip().lower()
            if title in seen_titles:
                issues.append(f"duplicate title at index {idx}: {item.get('title')}")
            seen_titles.add(title)

            hashtags = item.get("hashtags", [])
            if len(hashtags) != len(set(hashtags)):
                issues.append(f"duplicate hashtags at index {idx}")
            if len(hashtags) < 5:
                issues.append(f"low hashtag count at index {idx}")

            desc = str(item.get("description", "")).strip()
            if len(desc) < 30:
                issues.append(f"short description at index {idx}")

            if not str(item.get("source", "")).strip():
                issues.append(f"empty source at index {idx}")
            if float(item.get("trend_score", 0)) < 7:
                issues.append(f"low trend_score at index {idx}")

        return {
            "total_items": len(self.contents),
            "issues_count": len(issues),
            "issues_sample": issues[:20],
        }


    def top_trending_content(self, limit: int = 10, bucket: Optional[str] = None) -> List[RankedContent]:
        if limit <= 0:
            return []
        if bucket and bucket not in VALID_BUCKETS:
            raise ValueError(f"bucket must be one of {sorted(VALID_BUCKETS)}")

        rows = list(enumerate(self.contents))
        if bucket:
            filtered = [(i, c) for i, c in rows if c.get("posting_time") == bucket]
            if filtered:
                rows = filtered

        rows.sort(key=lambda x: float(x[1].get("trend_score", 0)), reverse=True)
        return [RankedContent(content_id=i, item=c, score=float(c.get("trend_score", 0))) for i, c in rows[:limit]]

    def rank_candidates(
        self,
        posting_time: Optional[str] = None,
        top_n: int = 5,
        cooldown_hours: int = 24,
        exclude_ids: Optional[Iterable[int]] = None,
    ) -> List[RankedContent]:
        if posting_time and posting_time not in VALID_BUCKETS:
            raise ValueError(f"posting_time must be one of {sorted(VALID_BUCKETS)}")
        if top_n <= 0:
            return []

        blocked = set(exclude_ids or set())
        feedback_avg, posts_count, recent_ids, cooldown_ids = self._metrics_snapshot(cooldown_hours=cooldown_hours)
        candidates = list(enumerate(self.contents))
        if posting_time:
            filtered = [(i, c) for i, c in candidates if c["posting_time"] == posting_time]
            if filtered:
                candidates = filtered

        if blocked:
            filtered = [(i, c) for i, c in candidates if i not in blocked]
            if filtered:
                candidates = filtered

        if cooldown_hours > 0:
            limited = [(i, c) for i, c in candidates if i not in cooldown_ids]
            if limited:
                candidates = limited

        ranked: List[RankedContent] = []
        for idx, content in candidates:
            trend_score = float(content["trend_score"])
            feedback = feedback_avg.get(idx, 0.0)
            published = posts_count.get(idx, 0)
            freshness_bonus = 2.0 if idx not in recent_ids else -3.0
            exploration_bonus = 1.5 if published == 0 else max(0.0, 1.0 / published)
            trend_weight = 0.7 if feedback < 6 else 0.5
            feedback_weight = 0.3 if feedback < 6 else 0.5
            diversity_penalty = min(1.5, published * 0.2)
            seasonal_bonus = self._seasonal_bonus(content)
            score = (
                trend_score * trend_weight
                + feedback * feedback_weight
                + freshness_bonus
                + exploration_bonus
                - diversity_penalty
                + seasonal_bonus
                + random.uniform(-0.25, 0.25)
            )
            ranked.append(RankedContent(content_id=idx, item=content, score=score))

        ranked.sort(key=lambda r: r.score, reverse=True)
        return ranked[:top_n]

    def pick_next(self, posting_time: Optional[str] = None, cooldown_hours: int = 24) -> RankedContent:
        ranked = self.rank_candidates(posting_time=posting_time, top_n=1, cooldown_hours=cooldown_hours)
        if not ranked:
            raise RuntimeError("No content candidates available")
        return ranked[0]

    def plan_for_buckets(self, buckets: List[str], cooldown_hours: int = 24) -> Dict[str, RankedContent]:
        used: set[int] = set()
        plan: Dict[str, RankedContent] = {}
        for bucket in buckets:
            if bucket not in VALID_BUCKETS:
                continue
            ranked = self.rank_candidates(
                posting_time=bucket,
                top_n=1,
                cooldown_hours=cooldown_hours,
                exclude_ids=used,
            )
            if ranked:
                plan[bucket] = ranked[0]
                used.add(ranked[0].content_id)
        return plan

    def recommend_best_bucket(self) -> str:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT bucket, AVG(score) FROM posts WHERE bucket IS NOT NULL GROUP BY bucket"
            ).fetchall()
        if not rows:
            return "день"
        rows.sort(key=lambda r: float(r[1] or 0), reverse=True)
        bucket = rows[0][0]
        return bucket if bucket in VALID_BUCKETS else "день"

    def recommend_best_style(self, bucket: Optional[str] = None) -> str:
        with sqlite3.connect(self.db_path) as conn:
            if bucket in VALID_BUCKETS:
                rows = conn.execute(
                    "SELECT style, AVG(score) FROM posts WHERE style IS NOT NULL AND bucket = ? GROUP BY style",
                    (bucket,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT style, AVG(score) FROM posts WHERE style IS NOT NULL GROUP BY style"
                ).fetchall()
        rows = [r for r in rows if r[0] in VALID_STYLES]
        if not rows:
            return "viral"
        rows.sort(key=lambda r: float(r[1] or 0), reverse=True)
        return rows[0][0]

    def recommend_best_platform(self) -> str:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT platform, AVG(score) FROM posts WHERE platform IS NOT NULL GROUP BY platform"
            ).fetchall()
        rows = [r for r in rows if r[0]]
        if not rows:
            return "Telegram"
        rows.sort(key=lambda r: float(r[1] or 0), reverse=True)
        return str(rows[0][0])

    def get_content(self, content_id: int) -> Dict[str, Any]:
        if content_id < 0 or content_id >= len(self.contents):
            raise ValueError("content_id out of range")
        return self.contents[content_id]

    def mark_posted(
        self,
        content_id: int,
        score: float,
        bucket: Optional[str] = None,
        style: Optional[str] = None,
    ) -> None:
        content = self.get_content(content_id)
        style_to_store = style if style in VALID_STYLES else None
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO posts(content_id, title, posted_at, score, bucket, platform, style)
                VALUES (?, ?, datetime('now'), ?, ?, ?, ?)
                """,
                (
                    content_id,
                    content["title"],
                    round(score, 4),
                    bucket or content.get("posting_time"),
                    content.get("platform_recommendation"),
                    style_to_store,
                ),
            )
            conn.commit()
        self._invalidate_snapshot_cache()

    def add_feedback(self, content_id: int, score: float) -> None:
        self.get_content(content_id)
        if not (1 <= score <= 10):
            raise ValueError("feedback score must be from 1 to 10")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO feedback(content_id, value, created_at) VALUES (?, ?, datetime('now'))",
                (content_id, score),
            )
            conn.commit()
        self._invalidate_snapshot_cache()


    def weekly_strategy(self) -> Dict[str, Any]:
        # Lightweight 7-day blueprint: rotate by best bucket and keep diversity
        best_bucket = self.recommend_best_bucket()
        bucket_cycle = [best_bucket, "утро", "день", "вечер", "день", "утро", "вечер"]
        used: set[int] = set()
        days = []
        for i, bucket in enumerate(bucket_cycle, 1):
            ranked = self.rank_candidates(posting_time=bucket, top_n=1, cooldown_hours=24, exclude_ids=used)
            if ranked:
                pick = ranked[0]
                used.add(pick.content_id)
                days.append({
                    "day": i,
                    "bucket": bucket,
                    "content_id": pick.content_id,
                    "title": pick.item.get("title"),
                    "score": round(pick.score, 2),
                    "style": self.recommend_best_style(bucket),
                })
            else:
                days.append({"day": i, "bucket": bucket, "content_id": None, "title": None, "score": None, "style": self.recommend_best_style(bucket)})

        return {
            "best_bucket": best_bucket,
            "best_platform": self.recommend_best_platform(),
            "days": days,
        }

    def optimization_tips(self) -> Dict[str, Any]:
        s = self.stats()
        monetization_ready = bool(self.get_runtime_config("offer_url", ""))
        best_bucket = self.recommend_best_bucket()
        best_style = self.recommend_best_style(best_bucket)
        best_platform = self.recommend_best_platform()

        tips = [
            f"Сфокусируйся на публикациях в окне: {best_bucket}",
            f"Базовый стиль для роста сейчас: {best_style}",
            f"Лучше всего заходит платформа-референс: {best_platform}",
        ]
        if not monetization_ready:
            tips.append("Добавь OFFER_URL и включи monetization для захвата трафика")
        if s["avg_feedback"] < 7:
            tips.append("Подними интерактив: вопрос в конце + опрос в комментариях")

        return {
            "best_bucket": best_bucket,
            "best_style": best_style,
            "best_platform": best_platform,
            "avg_feedback": s["avg_feedback"],
            "tips": tips,
        }


    def recent_posts(self, limit: int = 10) -> List[Dict[str, Any]]:
        limit = max(1, min(100, int(limit)))
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, content_id, title, posted_at, score, bucket, platform, style
                FROM posts
                ORDER BY posted_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [
            {
                "id": r[0],
                "content_id": r[1],
                "title": r[2],
                "posted_at": r[3],
                "score": r[4],
                "bucket": r[5],
                "platform": r[6],
                "style": r[7],
            }
            for r in rows
        ]

    def cleanup_old_data(self, retention_days: int = 180) -> Dict[str, int]:
        retention_days = max(1, int(retention_days))
        with sqlite3.connect(self.db_path) as conn:
            old_posts = conn.execute(
                "SELECT COUNT(*) FROM posts WHERE posted_at < datetime('now', ?)",
                (f"-{retention_days} days",),
            ).fetchone()[0]
            old_feedback = conn.execute(
                "SELECT COUNT(*) FROM feedback WHERE created_at < datetime('now', ?)",
                (f"-{retention_days} days",),
            ).fetchone()[0]

            conn.execute(
                "DELETE FROM posts WHERE posted_at < datetime('now', ?)",
                (f"-{retention_days} days",),
            )
            conn.execute(
                "DELETE FROM feedback WHERE created_at < datetime('now', ?)",
                (f"-{retention_days} days",),
            )
            conn.commit()

        self._invalidate_snapshot_cache()
        return {"deleted_posts": int(old_posts or 0), "deleted_feedback": int(old_feedback or 0)}

    def stats(self) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            total_posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
            total_feedback = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
            avg_feedback = conn.execute("SELECT AVG(value) FROM feedback").fetchone()[0] or 0
            avg_pick_score = conn.execute("SELECT AVG(score) FROM posts").fetchone()[0] or 0
            top = conn.execute(
                "SELECT content_id, AVG(value) avg_score, COUNT(*) c FROM feedback GROUP BY content_id ORDER BY avg_score DESC, c DESC LIMIT 5"
            ).fetchall()
            by_bucket = conn.execute(
                "SELECT bucket, COUNT(*) cnt, AVG(score) FROM posts GROUP BY bucket ORDER BY cnt DESC"
            ).fetchall()
            by_style = conn.execute(
                "SELECT style, COUNT(*) cnt, AVG(score) FROM posts GROUP BY style ORDER BY cnt DESC"
            ).fetchall()
        return {
            "total_posts": total_posts,
            "total_feedback": total_feedback,
            "avg_feedback": round(float(avg_feedback), 2),
            "avg_pick_score": round(float(avg_pick_score), 2),
            "top": top,
            "by_bucket": by_bucket,
            "by_style": by_style,
        }


def _hook_line(item: Dict[str, Any]) -> str:
    return random.choice(
        [
            f"⚡ {item['title']} — формат, который обычно дает лучший retention",
            f"🔥 {item['title']} — быстрый контент-план на сегодня",
            f"🚀 {item['title']} — идея под тренды и вовлечение",
        ]
    )


def _engagement_prompt(item: Dict[str, Any]) -> str:
    goal = str(item.get("content_goal", "")).lower()
    if "обуч" in goal:
        return "💬 Напиши в комментариях, какой пункт разобрать следующим."
    if "мотив" in goal:
        return "💬 Если заряжает — поставь 🔥 и поделись с другом."
    if "развес" in goal:
        return "💬 Оцени по шкале от 1 до 10, насколько жизненно."
    return "💬 Напиши, сработает ли это у тебя сегодня."


def format_post(content_id: int, item: Dict[str, Any], style: str = "viral") -> str:
    if style not in VALID_STYLES:
        style = "viral"

    hashtags = " ".join(html.escape(tag) for tag in item["hashtags"])
    variation_line = html.escape(random.choice(item["variation"]))
    engagement_line = _engagement_prompt(item)

    if style == "concise":
        return (
            f"<b>{html.escape(item['title'])}</b>\n{html.escape(item['description'])}\n\n"
            f"🧪 {variation_line}\n{html.escape(item['call_to_action'])}\n\n"
            f"{engagement_line}\n\n{hashtags}\n#content_id_{content_id}"
        )

    if style == "story":
        return (
            f"{html.escape(_hook_line(item))}\n\n"
            f"1) Контекст: {html.escape(item['description'])}\n"
            f"2) Действие: {html.escape(item['script'])}\n"
            f"3) Усиление: {variation_line}\n"
            f"4) CTA: {html.escape(item['call_to_action'])}\n\n"
            f"{engagement_line}\n\n{hashtags}\n#content_id_{content_id}"
        )

    if style == "checklist":
        return (
            f"✅ <b>{html.escape(item['title'])}</b>\n\n"
            f"• Формат: {html.escape(item['content_type'])}\n"
            f"• Цель: {html.escape(item['content_goal'])}\n"
            f"• Тренд: {item['trend_score']}/10\n"
            f"• Сценарий: {html.escape(item['script'])}\n"
            f"• A/B: {variation_line}\n"
            f"• CTA: {html.escape(item['call_to_action'])}\n\n"
            f"{engagement_line}\n\n{hashtags}\n#content_id_{content_id}"
        )

    return (
        f"{html.escape(_hook_line(item))}\n\n"
        f"🎬 <b>Сценарий:</b>\n{html.escape(item['script'])}\n\n"
        f"🧪 <b>A/B вариант:</b> {variation_line}\n"
        f"🎯 <b>Цель:</b> {html.escape(item['content_goal'])}\n"
        f"📈 <b>Тренд-оценка:</b> {item['trend_score']}/10\n"
        f"🎨 <b>Визуал:</b> {html.escape(item['visual_cues'])}\n"
        f"🔊 <b>Аудио:</b> {html.escape(item['audio_cues'])}\n\n"
        f"{html.escape(item['call_to_action'])}\n\n"
        f"{engagement_line}\n\n{hashtags}\n#content_id_{content_id}"
    )


def get_posting_bucket(now: datetime) -> str:
    if now.hour < 12:
        return "утро"
    if now.hour < 18:
        return "день"
    return "вечер"
