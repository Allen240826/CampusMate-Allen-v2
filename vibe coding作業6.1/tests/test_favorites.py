import re
import tempfile
import unittest
from pathlib import Path

from app import create_app
from database import get_connection
from werkzeug.security import generate_password_hash


class CampusMateFavoriteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "favorites.sqlite3"
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "favorites-test-secret",
            "DATABASE": str(self.database_path),
        }, initialize_db=True)
        self.client_a = self.app.test_client()
        self.client_b = self.app.test_client()
        with get_connection(self.database_path) as db:
            users = []
            for email, name in (("member-a@example.edu", "Member A"), ("member-b@example.edu", "Member B")):
                cursor = db.execute(
                    "INSERT INTO users (email, display_name, password_hash) VALUES (?, ?, ?)",
                    (email, name, generate_password_hash("favorite-test-password")),
                )
                users.append(cursor.lastrowid)
            self.user_a, self.user_b = users
            self.scholarship_id = db.execute(
                "SELECT id FROM items WHERE seed_key='demo-scholarship'"
            ).fetchone()[0]
            self.event_id = db.execute(
                "SELECT id FROM items WHERE seed_key='demo-campus-events'"
            ).fetchone()[0]
            category_id = db.execute("SELECT id FROM categories WHERE slug='events'").fetchone()[0]
            cursor = db.execute(
                """INSERT INTO items (title, summary, content, category_id, created_by, is_public, is_demo)
                   VALUES ('私有活動', 'private summary', 'private content', ?, ?, 0, 1)""",
                (category_id, self.user_b),
            )
            self.private_b_id = cursor.lastrowid

    def tearDown(self):
        self.temp_dir.cleanup()

    def _csrf_token(self, client, page):
        response = client.get(page)
        self.assertEqual(response.status_code, 200)
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match, f"CSRF token missing from {page}")
        return match.group(1).decode()

    def _login(self, client, email):
        token = self._csrf_token(client, "/login")
        return client.post("/login", data={
            "csrf_token": token, "email": email, "password": "favorite-test-password",
        })

    def _favorite_post(self, client, action, item_id, return_to="/favorites", **extra):
        endpoint = f"/favorites/{item_id}/{action}"
        token = self._csrf_token(client, "/favorites")
        data = {"csrf_token": token, "next": return_to, **extra}
        return client.post(endpoint, data=data)

    def _count_favorite(self, user_id, item_id):
        with get_connection(self.database_path) as db:
            return db.execute(
                "SELECT COUNT(*) FROM favorites WHERE user_id=? AND item_id=?",
                (user_id, item_id),
            ).fetchone()[0]

    def test_member_can_add_duplicate_prevention_and_state_persists(self):
        self.assertEqual(self._login(self.client_a, "member-a@example.edu").status_code, 302)
        response = self._favorite_post(
            self.client_a, "add", self.scholarship_id, "/explore?q=%E7%8D%8E%E5%AD%B8%E9%87%91",
            user_id=self.user_b,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/explore?q=%E7%8D%8E%E5%AD%B8%E9%87%91")
        self.assertEqual(self._count_favorite(self.user_a, self.scholarship_id), 1)
        self.assertEqual(self._count_favorite(self.user_b, self.scholarship_id), 0)

        repeated = self._favorite_post(self.client_a, "add", self.scholarship_id)
        self.assertEqual(repeated.status_code, 302)
        self.assertEqual(self._count_favorite(self.user_a, self.scholarship_id), 1)

        favorites_page = self.client_a.get("/favorites")
        self.assertIn("校內清寒助學金申請提醒".encode(), favorites_page.data)
        self.assertIn("♥".encode(), favorites_page.data)
        refreshed = self.client_a.get("/favorites")
        self.assertIn("校內清寒助學金申請提醒".encode(), refreshed.data)

        logout_token = self._csrf_token(self.client_a, "/favorites")
        self.client_a.post("/logout", data={"csrf_token": logout_token})
        self.assertEqual(self._login(self.client_a, "member-a@example.edu").status_code, 302)
        after_relogin = self.client_a.get("/favorites")
        self.assertIn("校內清寒助學金申請提醒".encode(), after_relogin.data)

    def test_member_isolation_and_cannot_remove_another_members_favorite(self):
        self._login(self.client_a, "member-a@example.edu")
        self._favorite_post(self.client_a, "add", self.scholarship_id)
        self._login(self.client_b, "member-b@example.edu")

        self.assertNotIn("校內清寒助學金申請提醒".encode(), self.client_b.get("/favorites").data)
        self.assertIn("還是空的".encode(), self.client_b.get("/favorites").data)

        self._favorite_post(self.client_b, "add", self.event_id)
        b_page = self.client_b.get("/favorites")
        self.assertIn("校園講座與藝文活動去哪裡找？".encode(), b_page.data)
        self.assertNotIn("校內清寒助學金申請提醒".encode(), b_page.data)

        # A crafts a remove request for B's item; the server still scopes DELETE to A's session ID.
        self._favorite_post(self.client_a, "remove", self.event_id)
        self.assertEqual(self._count_favorite(self.user_b, self.event_id), 1)
        self.assertEqual(self._count_favorite(self.user_a, self.event_id), 0)
        self.assertIn("校園講座與藝文活動去哪裡找？".encode(), self.client_b.get("/favorites").data)

    def test_official_resource_entry_can_be_favorited(self):
        self.assertEqual(self._login(self.client_a, "member-a@example.edu").status_code, 302)
        with get_connection(self.database_path) as db:
            official_id = db.execute(
                "SELECT id FROM items WHERE seed_key='official-ntu-financial-support'"
            ).fetchone()[0]
        response = self._favorite_post(self.client_a, "add", official_id)
        self.assertEqual(response.status_code, 302)
        favorites_page = self.client_a.get("/favorites")
        self.assertIn("臺大經濟支持與獎助學金入口".encode(), favorites_page.data)
        self.assertIn("osa.ntu.edu.tw".encode(), favorites_page.data)

    def test_cancel_favorite_and_empty_state(self):
        self._login(self.client_a, "member-a@example.edu")
        self._favorite_post(self.client_a, "add", self.scholarship_id)
        canceled = self._favorite_post(self.client_a, "remove", self.scholarship_id)
        self.assertEqual(canceled.status_code, 302)
        self.assertEqual(self._count_favorite(self.user_a, self.scholarship_id), 0)
        page = self.client_a.get("/favorites")
        self.assertIn("你的收藏清單還是空的".encode(), page.data)
        self.assertIn("探索校園資訊".encode(), page.data)

    def test_guests_private_items_invalid_ids_and_csrf_are_rejected(self):
        guest = self.app.test_client()
        # Obtain a token from this client's own session before attempting the POST.
        guest_token = self._csrf_token(guest, f"/items/{self.scholarship_id}")
        guest_response = guest.post(
            f"/favorites/{self.scholarship_id}/add",
            data={"csrf_token": guest_token, "next": f"/items/{self.scholarship_id}"},
        )
        self.assertEqual(guest_response.status_code, 302)
        self.assertIn("/login?next=", guest_response.headers["Location"])
        protected_favorites = guest.get("/favorites")
        self.assertEqual(protected_favorites.status_code, 302)
        self.assertIn("/login?next=/favorites", protected_favorites.headers["Location"])
        self.assertEqual(self._count_favorite(self.user_a, self.scholarship_id), 0)

        with get_connection(self.database_path) as db:
            db.execute("INSERT INTO favorites (user_id, item_id) VALUES (?, ?)", (self.user_b, self.event_id))
        guest_remove = guest.post(
            f"/favorites/{self.event_id}/remove",
            data={"csrf_token": guest_token, "next": "/favorites"},
        )
        self.assertEqual(guest_remove.status_code, 302)
        self.assertIn("/login?next=", guest_remove.headers["Location"])
        self.assertEqual(self._count_favorite(self.user_b, self.event_id), 1)

        self._login(self.client_a, "member-a@example.edu")
        missing = self._favorite_post(self.client_a, "add", 987654321)
        self.assertEqual(missing.status_code, 302)
        self.assertEqual(self._count_favorite(self.user_a, 987654321), 0)

        private = self._favorite_post(self.client_a, "add", self.private_b_id)
        self.assertEqual(private.status_code, 302)
        self.assertEqual(self._count_favorite(self.user_a, self.private_b_id), 0)

        missing_csrf = self.client_a.post(
            f"/favorites/{self.scholarship_id}/add", data={"next": "/favorites"}
        )
        self.assertEqual(missing_csrf.status_code, 400)
        self.assertEqual(self._count_favorite(self.user_a, self.scholarship_id), 0)

        self._favorite_post(self.client_a, "add", self.scholarship_id)
        missing_remove_csrf = self.client_a.post(
            f"/favorites/{self.scholarship_id}/remove", data={"next": "/favorites"}
        )
        self.assertEqual(missing_remove_csrf.status_code, 400)
        self.assertEqual(self._count_favorite(self.user_a, self.scholarship_id), 1)

    def test_search_detail_and_member_routes_remain_available(self):
        self._login(self.client_a, "member-a@example.edu")
        self.assertEqual(self.client_a.get("/explore?q=獎學金").status_code, 200)
        self.assertEqual(self.client_a.get("/explore?category=events").status_code, 200)
        self.assertEqual(self.client_a.get(f"/items/{self.event_id}").status_code, 200)
        self.assertEqual(self.client_a.get("/me").status_code, 200)


if __name__ == "__main__":
    unittest.main()
