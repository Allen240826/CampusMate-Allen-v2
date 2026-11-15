import re
import tempfile
import unittest
from pathlib import Path

from app import create_app
from database import get_connection
from werkzeug.security import generate_password_hash


class CampusInfoExploreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "explore.sqlite3"
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "explore-test-secret",
            "DATABASE": str(self.database_path),
        }, initialize_db=True)
        self.client = self.app.test_client()
        self.added_item_ids = {}
        with get_connection(self.database_path) as db:
            users = []
            for email, name in (("owner@example.edu", "Owner"), ("another@example.edu", "Another")):
                cursor = db.execute(
                    "INSERT INTO users (email, display_name, password_hash) VALUES (?, ?, ?)",
                    (email, name, generate_password_hash("test-password-123")),
                )
                users.append(cursor.lastrowid)
            self.owner_id, self.other_user_id = users

            self._insert_public_item(db, "courses", "課程規劃指南", "課程摘要", "特殊詞課程內容", "2026-01-02", "2026-06-01")
            self._insert_public_item(db, "announcements", "行政公告測試", "公告摘要", "行政內容", "2026-01-03", None)
            category_id = db.execute("SELECT id FROM categories WHERE slug='scholarship'").fetchone()[0]
            cursor = db.execute(
                """INSERT INTO items (title, summary, content, category_id, created_by, is_public, is_demo)
                   VALUES (?, ?, ?, ?, ?, 0, 1)""",
                ("擁有者私人資料", "private-needle-summary", "private-needle-content", category_id, self.owner_id),
            )
            self.private_item_id = cursor.lastrowid

    def _insert_public_item(self, db, slug, title, summary, content, created_at, important_date):
        category_id = db.execute("SELECT id FROM categories WHERE slug = ?", (slug,)).fetchone()[0]
        cursor = db.execute(
            """INSERT INTO items (title, summary, content, category_id, source_url,
                       important_date, is_public, is_demo, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?, ?)""",
            (title, summary, content, category_id, "https://example.edu/info", important_date, created_at, created_at),
        )
        self.added_item_ids[slug] = cursor.lastrowid

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_keyword_search_empty_search_no_match_and_guest_access(self):
        response = self.client.get("/explore?q=特殊詞課程")
        self.assertEqual(response.status_code, 200)
        self.assertIn("課程規劃指南".encode(), response.data)

        # The category name itself is searchable, so the original scholarship demo is found.
        scholarship = self.client.get("/explore?q=獎學金")
        self.assertIn("校內清寒助學金申請提醒".encode(), scholarship.data)

        empty = self.client.get("/explore")
        self.assertEqual(empty.status_code, 200)
        self.assertIn("共找到".encode(), empty.data)
        self.assertIn("生活服務".encode(), empty.data)

        missing = self.client.get("/explore?q=完全不存在的校園關鍵字")
        self.assertIn("目前找不到符合的資訊".encode(), missing.data)
        self.assertEqual(missing.data.count(b'class="item-card"'), 0)

        injection = self.client.get("/explore", query_string={"q": "' OR 1=1 --"})
        self.assertIn("目前找不到符合的資訊".encode(), injection.data)
        xss = self.client.get("/explore", query_string={"q": "<script>alert(1)</script>"})
        self.assertNotIn(b"<script>alert(1)</script>", xss.data)

    def test_filtered_results_offer_full_reset_without_leaking_private_data(self):
        response = self.client.get('/explore?q=獎學金&category=scholarship')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'active-filter-reset', response.data)
        self.assertIn('清除所有篩選'.encode(), response.data)
        self.assertNotIn('擁有者私人資料'.encode(), response.data)
        reset = self.client.get('/explore')
        self.assertEqual(reset.status_code, 200)
        self.assertNotIn(b'active-filter-reset', reset.data)
        self.assertIn('新生校園生活服務小整理'.encode(), reset.data)

    def test_each_category_and_combined_filters_and_sort(self):
        expected = {
            "scholarship": "校內清寒助學金申請提醒",
            "courses": "課程規劃指南",
            "events": "校園講座與藝文活動去哪裡找？",
            "announcements": "行政公告測試",
            "life-services": "新生校園生活服務小整理",
        }
        for slug, title in expected.items():
            with self.subTest(category=slug):
                response = self.client.get("/explore", query_string={"category": slug})
                self.assertEqual(response.status_code, 200)
                self.assertIn(title.encode(), response.data)
                self.assertIn("selected".encode(), response.data)

        combined = self.client.get("/explore", query_string={"q": "特殊詞", "category": "courses"})
        self.assertIn("課程規劃指南".encode(), combined.data)
        excluded = self.client.get("/explore", query_string={"q": "特殊詞", "category": "events"})
        self.assertIn("目前找不到符合的資訊".encode(), excluded.data)

        important = self.client.get("/explore", query_string={"sort": "important"})
        self.assertLess(important.data.find("課程規劃指南".encode()), important.data.find("行政公告測試".encode()))
        self.assertEqual(self.client.get("/explore?category=invalid").status_code, 404)

    def test_detail_public_404_and_private_owner_isolation(self):
        public_id = self.added_item_ids["courses"]
        public = self.client.get(f"/items/{public_id}?next=%2Fexplore%3Fq%3Dcourse")
        self.assertEqual(public.status_code, 200)
        for expected in ("課程規劃指南", "特殊詞課程內容", "https://example.edu/info", "建立時間"):
            self.assertIn(expected.encode(), public.data)
        self.assertIn("前往原始資訊來源".encode(), public.data)
        self.assertEqual(self.client.get("/items/999999").status_code, 404)

        # Neither guests nor another member can read the owner's private item by URL.
        self.assertEqual(self.client.get(f"/items/{self.private_item_id}").status_code, 404)
        other_client = self._logged_in_client("another@example.edu")
        self.assertEqual(other_client.get(f"/items/{self.private_item_id}").status_code, 404)

        # The owner may access their own private detail, while search remains public-only.
        owner_client = self._logged_in_client("owner@example.edu")
        own_private = owner_client.get(f"/items/{self.private_item_id}")
        self.assertEqual(own_private.status_code, 200)
        self.assertIn("擁有者私人資料".encode(), own_private.data)
        private_search = owner_client.get("/explore?q=private-needle")
        self.assertIn("目前找不到符合的資訊".encode(), private_search.data)
        self.assertNotIn("擁有者私人資料".encode(), private_search.data)

    def test_malformed_source_url_does_not_break_detail_page(self):
        with get_connection(self.database_path) as db:
            category_id = db.execute("SELECT id FROM categories WHERE slug='courses'").fetchone()[0]
            cursor = db.execute(
                """INSERT INTO items (title, summary, content, category_id, source_url, is_public, is_demo)
                   VALUES (?, ?, ?, ?, ?, 1, 1)""",
                ("格式錯誤來源測試", "詳情仍可讀", "保留其他資訊內容", category_id, "http://["),
            )
            item_id = cursor.lastrowid

        response = self.client.get(f"/items/{item_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("格式錯誤來源測試".encode(), response.data)
        self.assertIn("保留其他資訊內容".encode(), response.data)
        self.assertIn("來源連結目前無法使用".encode(), response.data)
        self.assertNotIn(b'href="http://["', response.data)

    def _logged_in_client(self, email):
        client = self.app.test_client()
        login_page = client.get("/login")
        token = re.search(rb'name="csrf_token" value="([^"]+)"', login_page.data).group(1).decode()
        response = client.post("/login", data={
            "csrf_token": token, "email": email, "password": "test-password-123",
        })
        self.assertEqual(response.status_code, 302)
        return client


if __name__ == "__main__":
    unittest.main()
