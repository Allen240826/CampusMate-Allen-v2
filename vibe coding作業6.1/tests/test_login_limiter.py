import re
import tempfile
import unittest
from pathlib import Path

from app import InMemoryLoginLimiter, create_app
from database import get_connection
from werkzeug.security import generate_password_hash


class LoginLimiterTests(unittest.TestCase):
    def test_fixed_window_expires_and_success_can_clear_attempts(self):
        now = [100.0]
        limiter = InMemoryLoginLimiter("test-secret", limit=2, window_seconds=10, clock=lambda: now[0])
        limiter.record_failure("127.0.0.1", "member@example.edu")
        limiter.record_failure("127.0.0.1", "member@example.edu")
        self.assertTrue(limiter.is_limited("127.0.0.1", "member@example.edu"))
        limiter.clear("127.0.0.1", "member@example.edu")
        self.assertFalse(limiter.is_limited("127.0.0.1", "member@example.edu"))
        limiter.record_failure("127.0.0.1", "member@example.edu")
        limiter.record_failure("127.0.0.1", "member@example.edu")
        now[0] += 10
        self.assertFalse(limiter.is_limited("127.0.0.1", "member@example.edu"))

    def test_login_throttles_after_generic_failures_without_account_enumeration(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "login.sqlite3"
            app = create_app({
                "TESTING": True,
                "SECRET_KEY": "test-limiter-secret",
                "DATABASE": str(database_path),
                "LOGIN_RATE_LIMIT": 3,
                "LOGIN_RATE_WINDOW_SECONDS": 60,
            }, initialize_db=True)
            client = app.test_client()
            with get_connection(database_path) as db:
                db.execute(
                    "INSERT INTO users (email, display_name, password_hash) VALUES (?, ?, ?)",
                    ("known@example.edu", "Known", generate_password_hash("correct-password")),
                )

            def login(email, password):
                page = client.get("/login")
                token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
                return client.post("/login", data={
                    "csrf_token": token, "email": email, "password": password,
                })

            known_failure = login("known@example.edu", "wrong-password")
            unknown_failure = login("missing@example.edu", "wrong-password")
            self.assertEqual(known_failure.status_code, 401)
            self.assertEqual(unknown_failure.status_code, 401)
            self.assertIn("Email 或密碼不正確".encode(), known_failure.data)
            self.assertIn("Email 或密碼不正確".encode(), unknown_failure.data)

            # Three failures for this pair make even a subsequently correct password wait.
            login("known@example.edu", "wrong-password")
            login("known@example.edu", "wrong-password")
            login("known@example.edu", "wrong-password")
            limited = login("known@example.edu", "correct-password")
            self.assertEqual(limited.status_code, 429)
            self.assertIn("登入嘗試次數過多".encode(), limited.data)
            self.assertNotIn("password_hash".encode(), limited.data)

            # A different client address has its own bucket and can still log in.
            fresh_client = app.test_client()
            page = fresh_client.get("/login")
            token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
            success = fresh_client.post("/login", data={
                "csrf_token": token, "email": "known@example.edu", "password": "correct-password",
            }, environ_base={"REMOTE_ADDR": "192.0.2.25"})
            self.assertEqual(success.status_code, 302)



if __name__ == "__main__":
    unittest.main()
