import tempfile
import unittest
from pathlib import Path

from engine import ContentEngine, VALID_BUCKETS, VALID_STYLES, format_post


class EngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp_dir.name) / "test_state.db"
        self.engine = ContentEngine(Path("content_ideas_2026_ru.json"), db_path=self.db)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_dataset_loaded(self):
        self.assertGreaterEqual(len(self.engine.contents), 30)

    def test_pick_next_returns_ranked(self):
        ranked = self.engine.pick_next("утро")
        self.assertIsInstance(ranked.content_id, int)
        self.assertIn("title", ranked.item)

    def test_rank_candidates_top_n(self):
        ranked = self.engine.rank_candidates(posting_time="день", top_n=3)
        self.assertEqual(len(ranked), 3)
        self.assertGreaterEqual(ranked[0].score, ranked[1].score)

    def test_invalid_bucket_raises(self):
        with self.assertRaises(ValueError):
            self.engine.rank_candidates(posting_time="ночь")

    def test_feedback_changes_stats(self):
        ranked = self.engine.pick_next()
        self.engine.mark_posted(ranked.content_id, ranked.score, bucket="день", style="viral")
        self.engine.add_feedback(ranked.content_id, 9)
        stats = self.engine.stats()
        self.assertEqual(stats["total_posts"], 1)
        self.assertEqual(stats["total_feedback"], 1)
        self.assertTrue(stats["by_bucket"])
        self.assertTrue(stats["by_style"])

    def test_feedback_range_validation(self):
        ranked = self.engine.pick_next()
        with self.assertRaises(ValueError):
            self.engine.add_feedback(ranked.content_id, 11)

    def test_posting_time_values_are_valid(self):
        for item in self.engine.contents:
            self.assertIn(item["posting_time"], VALID_BUCKETS)

    def test_format_post_contains_content_id(self):
        ranked = self.engine.pick_next()
        text = format_post(ranked.content_id, ranked.item)
        self.assertIn(f"#content_id_{ranked.content_id}", text)

    def test_all_styles_render(self):
        ranked = self.engine.pick_next()
        for style in VALID_STYLES:
            text = format_post(ranked.content_id, ranked.item, style=style)
            self.assertIn(f"#content_id_{ranked.content_id}", text)

    def test_best_bucket_default(self):
        self.assertEqual(self.engine.recommend_best_bucket(), "день")

    def test_best_style_default(self):
        self.assertEqual(self.engine.recommend_best_style(), "viral")

    def test_best_platform_default(self):
        self.assertEqual(self.engine.recommend_best_platform(), "Telegram")

    def test_cooldown_excludes_recent_content_if_possible(self):
        ranked = self.engine.pick_next(cooldown_hours=0)
        self.engine.mark_posted(ranked.content_id, ranked.score, bucket="день")
        next_pick = self.engine.pick_next(cooldown_hours=24)
        self.assertNotEqual(next_pick.content_id, ranked.content_id)

    def test_rank_zero_top_n(self):
        self.assertEqual(self.engine.rank_candidates(top_n=0), [])

    def test_runtime_config_roundtrip(self):
        self.engine.set_runtime_config("autopilot", "1")
        self.assertEqual(self.engine.get_runtime_config("autopilot"), "1")

    def test_plan_for_buckets_unique_ids(self):
        plan = self.engine.plan_for_buckets(["утро", "день", "вечер"], cooldown_hours=0)
        ids = [v.content_id for v in plan.values()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_audit_content_structure(self):
        report = self.engine.audit_content()
        self.assertIn("total_items", report)
        self.assertIn("issues_count", report)
        self.assertIn("issues_sample", report)


    def test_optimization_tips_structure(self):
        rep = self.engine.optimization_tips()
        self.assertIn("best_bucket", rep)
        self.assertIn("best_style", rep)
        self.assertIn("tips", rep)
        self.assertTrue(isinstance(rep["tips"], list))

    def test_format_post_has_engagement_prompt(self):
        ranked = self.engine.pick_next()
        text = format_post(ranked.content_id, ranked.item, style="viral")
        self.assertIn("💬", text)


    def test_top_trending_content(self):
        out = self.engine.top_trending_content(limit=5, bucket="утро")
        self.assertLessEqual(len(out), 5)
        if len(out) >= 2:
            self.assertGreaterEqual(float(out[0].item["trend_score"]), float(out[1].item["trend_score"]))


if __name__ == "__main__":
    unittest.main()
