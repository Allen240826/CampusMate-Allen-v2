import re
import threading
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app import create_app
from database import get_connection
from werkzeug.security import generate_password_hash


class CampusMatePrivateItemsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "private-items.sqlite3"
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "private-items-test-secret",
            "DATABASE": str(self.database_path),
        }, initialize_db=True)
        self.client_a = self.app.test_client()
        self.client_b = self.app.test_client()
        with get_connection(self.database_path) as db:
            users = []
            for email, name in (("owner-a@example.edu", "Owner A"), ("owner-b@example.edu", "Owner B")):
                cursor = db.execute(
                    "INSERT INTO users (email, display_name, password_hash) VALUES (?, ?, ?)",
                    (email, name, generate_password_hash("private-test-password")),
                )
                users.append(cursor.lastrowid)
            self.user_a, self.user_b = users
            self.public_item_id = db.execute(
                "SELECT id FROM items WHERE seed_key='demo-scholarship'"
            ).fetchone()[0]

    def tearDown(self):
        self.temp_dir.cleanup()

    def _csrf(self, client, path):
        response = client.get(path)
        self.assertEqual(response.status_code, 200)
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match, f"CSRF token missing from {path}")
        return match.group(1).decode()

    def _login(self, client, email):
        token = self._csrf(client, "/login")
        return client.post("/login", data={
            "csrf_token": token, "email": email, "password": "private-test-password",
        })

    def _form(self, **overrides):
        values = {
            "title": "我的選課提醒",
            "category_id": "2",
            "summary": "整理自己的選課準備事項。",
            "content": "記得先確認課程規定與時間。",
            "source_url": "https://example.edu/course-info",
            "important_date": "2026-12-01",
        }
        values.update(overrides)
        return values

    def _create_item(self, client, **overrides):
        token, submission_key = self._new_submission_form(client)
        return client.post("/my-items/new", data={
            "csrf_token": token, "submission_key": submission_key,
            **self._form(**overrides),
        })

    def _new_submission_form(self, client):
        response = client.get("/my-items/new")
        self.assertEqual(response.status_code, 200)
        csrf_match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        key_match = re.search(rb'name="submission_key" value="([^"]+)"', response.data)
        self.assertIsNotNone(csrf_match)
        self.assertIsNotNone(key_match)
        self.assertIn(b'data-submit-once', response.data)
        self.assertIn(b'data-saving-label="', response.data)
        self.assertIn(b'aria-live="polite"', response.data)
        return csrf_match.group(1).decode(), key_match.group(1).decode()

    def _item_count(self, title):
        with get_connection(self.database_path) as db:
            return db.execute("SELECT COUNT(*) FROM items WHERE title = ?", (title,)).fetchone()[0]

    def test_editor_counts_render_existing_values_without_disabling_csrf(self):
        self._login(self.client_a, 'owner-a@example.edu')
        fresh = self.client_a.get('/my-items/new')
        self.assertIn('最多 150 字元'.encode(), fresh.data)
        self.assertIn(b'id="title-count"', fresh.data)
        self.assertIn(b'name="csrf_token"', fresh.data)
        created = self._create_item(self.client_a, title='計數測試', summary='四字摘要')
        item_id = int(created.headers['Location'].split('/items/')[1].split('?')[0])
        edit = self.client_a.get(f'/my-items/{item_id}/edit')
        self.assertIn('4 / 150 字元'.encode(), edit.data)
        self.assertIn('4 / 500 字元'.encode(), edit.data)
        self.assertEqual(self.client_a.post(f'/my-items/{item_id}/edit', data=self._form()).status_code, 400)

    def test_owner_can_create_private_item_and_data_persists(self):
        self._login(self.client_a, "owner-a@example.edu")
        response = self._create_item(
            self.client_a, created_by=str(self.user_b), user_id=str(self.user_b),
            is_public="1", is_demo="1",
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/items/", response.headers["Location"])
        with get_connection(self.database_path) as db:
            item = db.execute("SELECT * FROM items WHERE title='我的選課提醒'").fetchone()
            self.assertIsNotNone(item)
            self.assertEqual(item["created_by"], self.user_a)
            self.assertEqual(item["is_public"], 0)
            self.assertEqual(item["is_demo"], 0)
            self.assertTrue(item["created_at"])
            self.assertTrue(item["updated_at"])
            self.item_id = item["id"]

        detail = self.client_a.get(response.headers["Location"])
        self.assertEqual(detail.status_code, 200)
        self.assertIn("我的選課提醒".encode(), detail.data)
        self.assertIn("私人資訊已新增".encode(), detail.data)
        self.assertIn("https://example.edu/course-info".encode(), detail.data)
        self.assertEqual(self.client_a.get("/my-items").data.count(b"personal-item-card"), 1)

        refreshed = self.client_a.get("/my-items")
        self.assertIn("我的選課提醒".encode(), refreshed.data)
        restarted_app = create_app({
            "TESTING": True,
            "SECRET_KEY": "private-items-test-secret",
            "DATABASE": str(self.database_path),
        }, initialize_db=True)
        restarted_client = restarted_app.test_client()
        self._login(restarted_client, "owner-a@example.edu")
        self.assertIn("我的選課提醒".encode(), restarted_client.get("/my-items").data)

    def test_replayed_create_post_is_idempotent_but_new_form_allows_same_content(self):
        self._login(self.client_a, "owner-a@example.edu")
        csrf_token, submission_key = self._new_submission_form(self.client_a)
        data = {
            "csrf_token": csrf_token,
            "submission_key": submission_key,
            **self._form(title="重複送出保護驗收"),
        }

        first = self.client_a.post("/my-items/new", data=data)
        self.assertEqual(first.status_code, 302)
        self.assertIn("/items/", first.headers["Location"])
        self.assertEqual(self._item_count("重複送出保護驗收"), 1)

        replay = self.client_a.post("/my-items/new", data=data)
        self.assertEqual(replay.status_code, 302)
        self.assertEqual(replay.headers["Location"], first.headers["Location"])
        self.assertEqual(self._item_count("重複送出保護驗收"), 1)
        self.assertEqual(self.client_a.get(first.headers["Location"]).status_code, 200)
        self.assertEqual(self._item_count("重複送出保護驗收"), 1)

        next_csrf, next_key = self._new_submission_form(self.client_a)
        self.assertNotEqual(next_key, submission_key)
        intentional_duplicate = self.client_a.post("/my-items/new", data={
            "csrf_token": next_csrf,
            "submission_key": next_key,
            **self._form(title="重複送出保護驗收"),
        })
        self.assertEqual(intentional_duplicate.status_code, 302)
        self.assertNotEqual(intentional_duplicate.headers["Location"], first.headers["Location"])
        self.assertEqual(self._item_count("重複送出保護驗收"), 2)

    def test_concurrent_retries_with_same_key_create_one_item(self):
        self._login(self.client_a, "owner-a@example.edu")
        csrf_a, submission_key = self._new_submission_form(self.client_a)
        concurrent_client = self.app.test_client()
        self._login(concurrent_client, "owner-a@example.edu")
        csrf_concurrent, _ = self._new_submission_form(concurrent_client)
        barrier = threading.Barrier(2)

        def submit(client, csrf_token):
            barrier.wait(timeout=3)
            return client.post("/my-items/new", data={
                "csrf_token": csrf_token,
                "submission_key": submission_key,
                **self._form(title="同時連點驗收"),
            })

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(submit, self.client_a, csrf_a)
            second = pool.submit(submit, concurrent_client, csrf_concurrent)
            responses = (first.result(timeout=10), second.result(timeout=10))

        self.assertEqual([response.status_code for response in responses], [302, 302])
        self.assertEqual(responses[0].headers["Location"], responses[1].headers["Location"])
        self.assertEqual(self._item_count("同時連點驗收"), 1)

    def test_submission_key_is_scoped_to_current_member(self):
        self._login(self.client_a, "owner-a@example.edu")
        csrf_a, shared_key = self._new_submission_form(self.client_a)
        response_a = self.client_a.post("/my-items/new", data={
            "csrf_token": csrf_a, "submission_key": shared_key,
            **self._form(title="會員提交隔離驗收"),
        })
        self.assertEqual(response_a.status_code, 302)

        self._login(self.client_b, "owner-b@example.edu")
        csrf_b, _ = self._new_submission_form(self.client_b)
        response_b = self.client_b.post("/my-items/new", data={
            "csrf_token": csrf_b, "submission_key": shared_key,
            **self._form(title="會員提交隔離驗收"),
        })
        self.assertEqual(response_b.status_code, 302)
        self.assertNotEqual(response_a.headers["Location"], response_b.headers["Location"])
        with get_connection(self.database_path) as db:
            owners = db.execute(
                "SELECT created_by FROM items WHERE title = ? ORDER BY created_by",
                ("會員提交隔離驗收",),
            ).fetchall()
        self.assertEqual([row["created_by"] for row in owners], [self.user_a, self.user_b])

    def test_storage_failure_keeps_form_retryable_with_same_submission_key(self):
        self._login(self.client_a, "owner-a@example.edu")
        csrf_token, submission_key = self._new_submission_form(self.client_a)
        with get_connection(self.database_path) as db:
            db.execute("""CREATE TRIGGER reject_retryable_item
                BEFORE INSERT ON items
                WHEN NEW.title = '暫時失敗後可重試'
                BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END""")

        payload = {
            "csrf_token": csrf_token, "submission_key": submission_key,
            **self._form(title="暫時失敗後可重試"),
        }
        failed = self.client_a.post("/my-items/new", data=payload)
        self.assertEqual(failed.status_code, 503)
        self.assertIn("儲存未完成".encode(), failed.data)
        self.assertIn(f'name="submission_key" value="{submission_key}"'.encode(), failed.data)
        self.assertEqual(self._item_count("暫時失敗後可重試"), 0)
        with get_connection(self.database_path) as db:
            db.execute("DROP TRIGGER reject_retryable_item")

        retried = self.client_a.post("/my-items/new", data=payload)
        self.assertEqual(retried.status_code, 302)
        self.assertEqual(self._item_count("暫時失敗後可重試"), 1)

    def test_edit_changes_fields_and_updated_timestamp(self):
        self._login(self.client_a, "owner-a@example.edu")
        self._create_item(self.client_a)
        with get_connection(self.database_path) as db:
            item = db.execute("SELECT id FROM items WHERE title='我的選課提醒'").fetchone()
            item_id = item["id"]
            db.execute("UPDATE items SET updated_at='2000-01-01T00:00:00Z' WHERE id=?", (item_id,))

        edit_page = self.client_a.get(f"/my-items/{item_id}/edit")
        self.assertEqual(edit_page.status_code, 200)
        self.assertIn("value=\"我的選課提醒\"".encode(), edit_page.data)
        token = self._csrf(self.client_a, f"/my-items/{item_id}/edit")
        edited = self._form(title="更新後的選課提醒", important_date="2026-12-15")
        response = self.client_a.post(f"/my-items/{item_id}/edit", data={"csrf_token": token, **edited})
        self.assertEqual(response.status_code, 302)
        with get_connection(self.database_path) as db:
            row = db.execute("SELECT title, important_date, updated_at FROM items WHERE id=?", (item_id,)).fetchone()
            self.assertEqual(row["title"], "更新後的選課提醒")
            self.assertEqual(row["important_date"], "2026-12-15")
            self.assertGreater(row["updated_at"], "2000-01-01T00:00:00Z")

    def test_only_owner_can_view_edit_or_delete_and_delete_is_confirmed(self):
        self._login(self.client_a, "owner-a@example.edu")
        self._create_item(self.client_a)
        with get_connection(self.database_path) as db:
            item_id = db.execute("SELECT id FROM items WHERE title='我的選課提醒'").fetchone()[0]

        # Another member cannot find this item in their list or access its detail/editor/delete URL.
        self._login(self.client_b, "owner-b@example.edu")
        self.assertNotIn("我的選課提醒".encode(), self.client_b.get("/my-items").data)
        self.assertEqual(self.client_b.get(f"/items/{item_id}").status_code, 404)
        self.assertEqual(self.client_b.get(f"/my-items/{item_id}/edit").status_code, 404)
        self.assertEqual(self.client_b.get(f"/my-items/{item_id}/delete").status_code, 404)

        token_b = self._csrf(self.client_b, "/my-items")
        attempted_edit = self.client_b.post(
            f"/my-items/{item_id}/edit",
            data={"csrf_token": token_b, **self._form(title="被竄改")},
        )
        attempted_delete = self.client_b.post(
            f"/my-items/{item_id}/delete", data={"csrf_token": token_b},
        )
        self.assertEqual(attempted_edit.status_code, 404)
        self.assertEqual(attempted_delete.status_code, 404)
        self.assertEqual(self._item_count("我的選課提醒"), 1)
        self.assertEqual(self._item_count("被竄改"), 0)

        # Owner sees a confirmation page; GET does not delete. A valid POST then removes it.
        confirm = self.client_a.get(f"/my-items/{item_id}/delete")
        self.assertEqual(confirm.status_code, 200)
        self.assertIn("確定要刪除".encode(), confirm.data)
        self.assertEqual(self._item_count("我的選課提醒"), 1)
        token_a = self._csrf(self.client_a, f"/my-items/{item_id}/delete")
        deleted = self.client_a.post(f"/my-items/{item_id}/delete", data={"csrf_token": token_a})
        self.assertEqual(deleted.status_code, 302)
        self.assertIn("/my-items", deleted.headers["Location"])
        self.assertEqual(self._item_count("我的選課提醒"), 0)
        self.assertEqual(self._item_count("校內清寒助學金申請提醒"), 1)

    def test_guests_validation_and_csrf(self):
        guest = self.app.test_client()
        for path in ("/my-items", "/my-items/new", "/my-items/1/edit", "/my-items/1/delete"):
            response = guest.get(path)
            self.assertEqual(response.status_code, 302, path)
            self.assertIn("/login?next=", response.headers["Location"])

        guest_token = self._csrf(guest, "/login")
        guest_post = guest.post(
            "/my-items/new", data={"csrf_token": guest_token, **self._form()}
        )
        self.assertEqual(guest_post.status_code, 302)
        public_page_token = self._csrf(guest, f"/items/{self.public_item_id}")
        guest_edit = guest.post(
            "/my-items/987654321/edit",
            data={"csrf_token": public_page_token, **self._form(title="未登入修改")},
        )
        guest_delete = guest.post(
            "/my-items/987654321/delete", data={"csrf_token": public_page_token},
        )
        self.assertEqual(guest_edit.status_code, 302)
        self.assertEqual(guest_delete.status_code, 302)
        self.assertEqual(self._item_count("我的選課提醒"), 0)

        self._login(self.client_a, "owner-a@example.edu")
        blank = self._create_item(self.client_a, title="   ", summary="", content="")
        self.assertEqual(blank.status_code, 400)
        self.assertEqual(self._item_count("我的選課提醒"), 0)

        bad_date = self._create_item(self.client_a, important_date="2026-02-31")
        self.assertEqual(bad_date.status_code, 400)
        bad_url = self._create_item(self.client_a, source_url="javascript:alert(1)")
        self.assertEqual(bad_url.status_code, 400)
        bad_category = self._create_item(self.client_a, category_id="99999")
        self.assertEqual(bad_category.status_code, 400)
        long_content = self._create_item(self.client_a, content="x" * 20001)
        self.assertEqual(long_content.status_code, 400)
        self.assertEqual(self._item_count("我的選課提醒"), 0)

        csrf_missing = self.client_a.post("/my-items/new", data=self._form())
        self.assertEqual(csrf_missing.status_code, 400)
        self.assertEqual(self._item_count("我的選課提醒"), 0)

    def test_private_content_never_appears_in_public_views_and_crud_does_not_break_features(self):
        self._login(self.client_a, "owner-a@example.edu")
        self._create_item(self.client_a, title="獨有私人搜尋標記")
        public_search = self.client_a.get("/explore?q=獨有私人搜尋標記")
        self.assertEqual(public_search.data.count(b'class="item-card"'), 0)
        self.assertIn("共找到 0 筆公開資訊".encode(), public_search.data)
        self.assertNotIn("獨有私人搜尋標記".encode(), self.client_a.get("/").data)

        self.assertEqual(self.client_a.get("/explore?q=獎學金").status_code, 200)
        self.assertEqual(self.client_a.get("/explore?category=events").status_code, 200)
        self.assertEqual(self.client_a.get(f"/items/{self.public_item_id}").status_code, 200)
        self.assertEqual(self.client_a.get("/favorites").status_code, 200)
        self.assertEqual(self.client_a.get("/my-items").status_code, 200)


if __name__ == "__main__":
    unittest.main()
