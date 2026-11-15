import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import create_app
from database import get_connection, initialize_database


class CampusMateAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "test.sqlite3"
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-only-secret-key",
            "DATABASE": str(self.database_path),
        }, initialize_db=True)
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def csrf_token(self, endpoint):
        response = self.client.get(endpoint)
        self.assertEqual(response.status_code, 200)
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match, f"CSRF token missing from {endpoint}")
        return match.group(1).decode()

    def register(self, **overrides):
        values = {
            "display_name": "Campus Tester",
            "email": "tester@example.edu",
            "password": "long-test-password",
            "password_confirm": "long-test-password",
        }
        values.update(overrides)
        values["csrf_token"] = self.csrf_token("/register")
        return self.client.post("/register", data=values, follow_redirects=True)

    def login(self, email="tester@example.edu", password="long-test-password"):
        token = self.csrf_token("/login")
        return self.client.post("/login", data={
            "csrf_token": token, "email": email, "password": password,
        }, follow_redirects=True)

    def test_registration_hashing_duplicate_email_and_validation(self):
        response = self.register()
        self.assertEqual(response.status_code, 200)
        self.assertIn("註冊成功".encode(), response.data)

        with get_connection(self.database_path) as db:
            user = db.execute("SELECT * FROM users").fetchone()
            self.assertEqual(user["email"], "tester@example.edu")
            self.assertNotEqual(user["password_hash"], "long-test-password")
            self.assertTrue(user["password_hash"].startswith("scrypt:"))

        duplicate = self.register(email="TESTER@example.edu")
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn("無法用於註冊".encode(), duplicate.data)

        invalid_email = self.register(email="not-an-email")
        self.assertEqual(invalid_email.status_code, 400)
        self.assertIn("有效的 Email".encode(), invalid_email.data)

        mismatch = self.register(password_confirm="different-password")
        self.assertEqual(mismatch.status_code, 400)
        self.assertIn("不一致".encode(), mismatch.data)

        too_short = self.register(password="short", password_confirm="short")
        self.assertEqual(too_short.status_code, 400)

    def test_login_session_logout_and_protected_profile(self):
        unauthenticated = self.client.get("/me")
        self.assertEqual(unauthenticated.status_code, 302)
        self.assertIn("/login", unauthenticated.headers["Location"])

        self.register()
        bad_login = self.login(password="incorrect-password")
        self.assertEqual(bad_login.status_code, 401)
        self.assertIn("Email 或密碼不正確".encode(), bad_login.data)

        good_login = self.login()
        self.assertEqual(good_login.status_code, 200)
        self.assertIn("嗨，Campus Tester".encode(), good_login.data)
        with self.client.session_transaction() as session:
            self.assertIn("user_id", session)

        # A new app instance simulates a process restart; SQLite retains the account.
        restarted_app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-only-secret-key",
            "DATABASE": str(self.database_path),
        }, initialize_db=True)
        restarted_client = restarted_app.test_client()
        login_page = restarted_client.get("/login")
        token = re.search(rb'name="csrf_token" value="([^"]+)"', login_page.data).group(1).decode()
        restarted_response = restarted_client.post("/login", data={
            "csrf_token": token, "email": "tester@example.edu", "password": "long-test-password",
        }, follow_redirects=True)
        self.assertIn("嗨，Campus Tester".encode(), restarted_response.data)

        logout_token = self.csrf_token("/me")
        logout_response = self.client.post("/logout", data={"csrf_token": logout_token}, follow_redirects=True)
        self.assertIn("安全登出".encode(), logout_response.data)
        with self.client.session_transaction() as session:
            self.assertNotIn("user_id", session)
        self.assertEqual(self.client.get("/me").status_code, 302)

    def test_homepage_and_csrf_protection(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.data.decode()
        self.assertEqual(html.count('class="category-card"'), 5)
        self.assertEqual(html.count('class="item-card"'), 6)
        self.assertIn("示範資料", html)
        self.assertIn("搜尋公開資訊、收藏重要內容".encode(), response.data)
        self.assertNotIn("後續階段陸續開放".encode(), response.data)
        self.assertNotIn("首頁原型".encode(), response.data)
        self.assertIn('href="/favorites"'.encode(), response.data)
        self.assertIn('href="/my-items"'.encode(), response.data)
        self.assertIn('aria-controls="primary-navigation"'.encode(), response.data)
        self.assertIn('id="primary-navigation"'.encode(), response.data)

        csrf_rejected = self.client.post("/logout", data={})
        self.assertEqual(csrf_rejected.status_code, 400)


class ItemOwnerMigrationTests(unittest.TestCase):
    def test_migration_preserves_rows_and_cascades_private_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            db = sqlite3.connect(path)
            try:
                db.execute("PRAGMA foreign_keys = ON")
                db.executescript("""
                    CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                        display_name TEXT NOT NULL, password_hash TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT '2026-01-01');
                    CREATE TABLE categories (id INTEGER PRIMARY KEY, slug TEXT UNIQUE NOT NULL,
                        name TEXT UNIQUE NOT NULL, icon TEXT NOT NULL, description TEXT NOT NULL);
                    CREATE TABLE items (id INTEGER PRIMARY KEY, seed_key TEXT UNIQUE, title TEXT NOT NULL,
                        summary TEXT NOT NULL, content TEXT NOT NULL, category_id INTEGER NOT NULL,
                        source_url TEXT, important_date TEXT, created_by INTEGER,
                        is_public INTEGER NOT NULL DEFAULT 0 CHECK (is_public IN (0,1)),
                        is_demo INTEGER NOT NULL DEFAULT 0 CHECK (is_demo IN (0,1)),
                        created_at TEXT NOT NULL DEFAULT '2026-01-01', updated_at TEXT NOT NULL DEFAULT '2026-01-01',
                        FOREIGN KEY(category_id) REFERENCES categories(id) ON DELETE RESTRICT,
                        FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL,
                        CHECK ((is_public=1 AND created_by IS NULL) OR (is_public=0 AND created_by IS NOT NULL)));
                    CREATE TABLE favorites (user_id INTEGER NOT NULL, item_id INTEGER NOT NULL,
                        created_at TEXT NOT NULL DEFAULT '2026-01-01', PRIMARY KEY(user_id,item_id),
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE);
                    INSERT INTO users VALUES (1,'owner@example.edu','Owner','hash','2026-01-01');
                    INSERT INTO categories VALUES (1,'private','私人','✦','test');
                    INSERT INTO items VALUES (1,'private-one','Private title','summary','content',1,NULL,NULL,1,0,0,'created','updated');
                    INSERT INTO items VALUES (2,'old-demo','Old demo','summary','content',1,'https://ntu.edu.tw','示範資料',NULL,1,1,'created','updated');
                    INSERT INTO favorites VALUES (1,1,'saved');
                """)
            finally:
                db.close()

            initialize_database(path)
            backups = list(Path(directory).glob("*-pre-owner-migration-*.sqlite3"))
            self.assertEqual(len(backups), 1)
            with get_connection(path) as db:
                fk = next(row for row in db.execute("PRAGMA foreign_key_list(items)") if row["from"] == "created_by")
                self.assertEqual(fk["on_delete"], "CASCADE")
                self.assertEqual(db.execute("SELECT title FROM items WHERE id=1").fetchone()[0], "Private title")
                self.assertEqual(db.execute("SELECT COUNT(*) FROM favorites").fetchone()[0], 1)
                self.assertIsNone(db.execute("SELECT important_date FROM items WHERE id=2").fetchone()[0])
                columns = {row["name"] for row in db.execute("PRAGMA table_info(items)")}
                self.assertIn("last_verified_at", columns)
                self.assertIn("is_current", columns)
                self.assertIsNone(db.execute("SELECT is_current FROM items WHERE id=2").fetchone()[0])
                self.assertEqual(db.execute("SELECT COUNT(*) FROM items WHERE seed_key LIKE 'official-ntu-%'").fetchone()[0], 5)
                db.execute("DELETE FROM users WHERE id=1")
                self.assertEqual(db.execute("SELECT COUNT(*) FROM items WHERE id=1").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM favorites").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
