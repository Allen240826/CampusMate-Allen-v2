import tempfile
import sqlite3
import unittest
from datetime import timedelta
from pathlib import Path

from app import create_app
from database import get_connection, initialize_database, taipei_today


class PublicContentQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "public-content.sqlite3"
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "public-content-test-secret",
            "DATABASE": str(self.database_path),
        }, initialize_db=True)
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_demo_dates_are_null_and_official_entries_seed_idempotently(self):
        with get_connection(self.database_path) as db:
            demos = db.execute(
                "SELECT COUNT(*) AS total, SUM(important_date IS NULL) AS no_date "
                "FROM items WHERE is_public=1 AND is_demo=1"
            ).fetchone()
            self.assertEqual(demos["total"], 5)
            self.assertEqual(demos["no_date"], 5)
            official = db.execute(
                "SELECT seed_key, title, summary, content, source_url, important_date, "
                "last_verified_at, is_current, is_demo FROM items "
                "WHERE seed_key LIKE 'official-ntu-%' ORDER BY seed_key"
            ).fetchall()
            self.assertEqual(len(official), 5)
            for item in official:
                self.assertEqual(item["is_demo"], 0)
                self.assertIsNone(item["important_date"])
                self.assertEqual(item["last_verified_at"], "2026-11-11")
                self.assertEqual(item["is_current"], 1)
                self.assertTrue(item["source_url"].startswith("https://"))
                self.assertTrue(item["title"] and item["summary"] and item["content"])

        initialize_database(self.database_path)
        with get_connection(self.database_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM items WHERE is_demo=1 AND is_public=1").fetchone()[0], 5)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM items WHERE seed_key LIKE 'official-ntu-%'").fetchone()[0], 5)

    def test_search_cards_and_details_show_sources_status_and_no_fake_dates(self):
        response = self.client.get("/explore?q=臺大")
        self.assertEqual(response.status_code, 200)
        for title in (
            "臺大經濟支持與獎助學金入口", "臺大學習資源入口",
            "臺大學生活動中心公告入口", "臺大校園公告系統", "臺大校園生活服務入口",
        ):
            self.assertIn(title.encode(), response.data)
        self.assertIn("台大官方來源".encode(), response.data)
        self.assertIn("未設定重要日期".encode(), response.data)
        self.assertIn("最後確認 · 2026-11-11".encode(), response.data)

        with get_connection(self.database_path) as db:
            official_id = db.execute(
                "SELECT id FROM items WHERE seed_key='official-ntu-financial-support'"
            ).fetchone()[0]
        detail = self.client.get(f"/items/{official_id}")
        self.assertEqual(detail.status_code, 200)
        for value in (
            "官方來源", "最後確認", "2026-11-11", "已確認仍適用",
            "未設定重要日期", "osa.ntu.edu.tw",
        ):
            self.assertIn(value.encode(), detail.data)
        self.assertNotIn("重要日期已過".encode(), detail.data)

    def test_three_current_states_and_passed_deadline_are_presented_separately(self):
        with get_connection(self.database_path) as db:
            course_id = db.execute(
                "SELECT id FROM items WHERE seed_key='official-ntu-learning-resources'"
            ).fetchone()[0]
            event_id = db.execute(
                "SELECT id FROM items WHERE seed_key='official-ntu-activity-center-board'"
            ).fetchone()[0]
            db.execute("UPDATE items SET is_current=NULL WHERE id=?", (course_id,))
            db.execute("UPDATE items SET is_current=0 WHERE id=?", (event_id,))
            db.execute(
                "UPDATE items SET important_date=? WHERE id=?",
                ((taipei_today() - timedelta(days=1)).isoformat(), course_id),
            )

        expired_detail = self.client.get(f"/items/{course_id}")
        self.assertIn("重要日期已過".encode(), expired_detail.data)
        self.assertIn("尚未確認".encode(), expired_detail.data)
        self.assertNotIn("已確認不再適用".encode(), expired_detail.data)
        card_status = self.client.get("/explore?q=臺大學生活動中心公告入口")
        self.assertIn("已確認不再適用".encode(), card_status.data)

        inactive_detail = self.client.get(f"/items/{event_id}")
        self.assertIn("已確認不再適用".encode(), inactive_detail.data)
        self.assertNotIn("重要日期已過".encode(), inactive_detail.data)

        with get_connection(self.database_path) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE items SET is_current=2 WHERE id=?", (course_id,))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE items SET last_verified_at='2026-02-30' WHERE id=?", (course_id,))

    def test_important_sort_places_future_then_expired_then_missing_dates(self):
        today = taipei_today()
        with get_connection(self.database_path) as db:
            category_id = db.execute(
                "SELECT id FROM categories WHERE slug='announcements'"
            ).fetchone()[0]
            test_rows = [
                ("未來期限排序測試", (today + timedelta(days=3)).isoformat()),
                ("已過期限排序測試", (today - timedelta(days=3)).isoformat()),
                ("無期限排序測試", None),
            ]
            for title, important_date in test_rows:
                db.execute(
                    """INSERT INTO items (title, summary, content, category_id, source_url,
                       important_date, is_public, is_demo, is_current)
                       VALUES (?, '測試摘要', '測試內容', ?, 'https://ann.cc.ntu.edu.tw/', ?, 1, 0, NULL)""",
                    (title, category_id, important_date),
                )

        response = self.client.get("/explore", query_string={"q": "期限排序測試", "sort": "important"})
        self.assertEqual(response.status_code, 200)
        positions = [response.data.find(title.encode()) for title, _ in test_rows]
        self.assertTrue(all(position >= 0 for position in positions))
        self.assertLess(positions[0], positions[1])
        self.assertLess(positions[1], positions[2])


if __name__ == "__main__":
    unittest.main()
