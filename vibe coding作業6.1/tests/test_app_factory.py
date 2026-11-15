import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app


class AppFactorySafetyTests(unittest.TestCase):
    def test_factory_does_not_initialize_database_unless_requested(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "deferred.sqlite3"
            config = {
                "TESTING": True,
                "SECRET_KEY": "factory-test-secret",
                "DATABASE": str(database_path),
            }
            create_app(config)
            self.assertFalse(database_path.exists())

            create_app(config, initialize_db=True)
            self.assertTrue(database_path.exists())

    def test_importing_app_module_does_not_create_or_initialize_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "must-not-be-created.sqlite3"
            code = (
                "import database; "
                f"database.DATABASE_PATH = {str(database_path)!r}; "
                "import app; "
                f"assert not __import__('pathlib').Path({str(database_path)!r}).exists()"
            )
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, "CAMPUSMATE_ENV": "development"},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_environment_is_case_insensitive_and_forces_debug_off(self):
        with tempfile.TemporaryDirectory() as directory:
            for value in ("Production", "production", "PRODUCTION"):
                with self.subTest(environment=value), patch.dict(os.environ, {
                    "CAMPUSMATE_ENV": value,
                    "CAMPUSMATE_SECRET_KEY": "production-test-secret",
                }, clear=False):
                    app = create_app({
                        "TESTING": True,
                        "DEBUG": True,
                        "SECRET_KEY": "test-config-override",
                        "DATABASE": str(Path(directory) / f"{value}.sqlite3"),
                    })
                    self.assertFalse(app.config["DEBUG"])
                    self.assertTrue(app.config["SESSION_COOKIE_SECURE"])
                    self.assertEqual(app.config["CAMPUSMATE_ENV"], "production")

    def test_missing_or_invalid_environment_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "Set CAMPUSMATE_ENV"):
                create_app()
        with patch.dict(os.environ, {
            "CAMPUSMATE_ENV": "prodution",
            "CAMPUSMATE_SECRET_KEY": "test-only-secret",
        }, clear=True):
            with self.assertRaisesRegex(RuntimeError, "Invalid CAMPUSMATE_ENV"):
                create_app()

    def test_explicit_development_mode_keeps_local_debug_available(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "CAMPUSMATE_ENV": "Development",
        }, clear=True):
            app = create_app({
                "TESTING": True,
                "SECRET_KEY": "development-test-secret",
                "DATABASE": str(Path(directory) / "dev.sqlite3"),
            })
            self.assertTrue(app.config["DEBUG"])
            self.assertFalse(app.config["SESSION_COOKIE_SECURE"])


if __name__ == "__main__":
    unittest.main()
