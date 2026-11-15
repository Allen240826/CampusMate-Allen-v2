"""CampusMate Flask application and account routes."""

from functools import wraps
from collections import OrderedDict, deque
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash

from database import (
    DATABASE_PATH,
    get_connection,
    get_homepage_data,
    get_item_for_view,
    get_user_favorites,
    initialize_database,
    search_public_items,
)

csrf = CSRFProtect()
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128
SUBMISSION_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


class InMemoryLoginLimiter:
    """Bounded per-process fixed-window limiter; stores only keyed digests."""

    def __init__(self, secret_key, limit=5, window_seconds=60, max_keys=10000, clock=time.monotonic):
        self._secret = secret_key.encode("utf-8") if isinstance(secret_key, str) else secret_key
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._clock = clock
        self._attempts = OrderedDict()
        self._lock = threading.Lock()

    def _key(self, remote_addr, email):
        value = f"{remote_addr or 'unknown'}\0{email.strip().lower()}".encode("utf-8")
        return hmac.new(self._secret, value, hashlib.sha256).digest()

    def is_limited(self, remote_addr, email):
        key = self._key(remote_addr, email)
        now = self._clock()
        with self._lock:
            attempts = self._attempts.get(key)
            if not attempts:
                return False
            while attempts and now - attempts[0] >= self.window_seconds:
                attempts.popleft()
            if not attempts:
                self._attempts.pop(key, None)
                return False
            self._attempts.move_to_end(key)
            return len(attempts) >= self.limit

    def record_failure(self, remote_addr, email):
        key = self._key(remote_addr, email)
        now = self._clock()
        with self._lock:
            attempts = self._attempts.setdefault(key, deque())
            while attempts and now - attempts[0] >= self.window_seconds:
                attempts.popleft()
            attempts.append(now)
            self._attempts.move_to_end(key)
            while len(self._attempts) > self.max_keys:
                self._attempts.popitem(last=False)

    def clear(self, remote_addr, email):
        with self._lock:
            self._attempts.pop(self._key(remote_addr, email), None)


def _safe_local_path(value, fallback="/"):
    """Accept local return paths only, rejecting scheme-relative and malformed URLs."""
    if (isinstance(value, str) and value.startswith("/") and not value.startswith("//")
            and "\\" not in value and "\r" not in value and "\n" not in value):
        return value
    return fallback


def _parse_private_item_form(form, categories):
    """Validate editor fields and return normalized, persistence-ready values."""
    data = {
        "title": form.get("title", "").strip(),
        "category_id": form.get("category_id", "").strip(),
        "summary": form.get("summary", "").strip(),
        "content": form.get("content", "").strip(),
        "source_url": form.get("source_url", "").strip(),
        "important_date": form.get("important_date", "").strip(),
    }
    errors = []
    if not data["title"]:
        errors.append("請填寫標題。")
    elif len(data["title"]) > 150:
        errors.append("標題不可超過 150 個字元。")
    if not data["summary"]:
        errors.append("請填寫摘要。")
    elif len(data["summary"]) > 500:
        errors.append("摘要不可超過 500 個字元。")
    if not data["content"]:
        errors.append("請填寫詳細內容。")
    elif len(data["content"]) > 20000:
        errors.append("詳細內容不可超過 20,000 個字元。")

    category_ids = {str(category["id"]) for category in categories}
    if data["category_id"] not in category_ids:
        errors.append("請選擇有效的資訊分類。")
    else:
        data["category_id"] = int(data["category_id"])

    if data["important_date"]:
        try:
            data["important_date"] = date.fromisoformat(data["important_date"]).isoformat()
        except ValueError:
            errors.append("重要日期格式無效，請使用日期選擇器輸入有效日期。")
    else:
        data["important_date"] = None

    if data["source_url"]:
        if len(data["source_url"]) > 2048:
            errors.append("來源網址不可超過 2,048 個字元。")
        else:
            try:
                parsed_url = urlsplit(data["source_url"])
                valid_url = parsed_url.scheme in {"http", "https"} and bool(parsed_url.hostname)
                if parsed_url.port is not None:
                    valid_url = valid_url and 0 < parsed_url.port < 65536
            except ValueError:
                valid_url = False
            if not valid_url:
                errors.append("來源網址必須是有效的 http 或 https 網址。")
    else:
        data["source_url"] = None
    return data, errors


def _development_secret(instance_path):
    """Persist a random local-only key without placing it in source control."""
    key_path = Path(instance_path) / ".secret_key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        return key_path.read_text(encoding="utf-8").strip()
    secret = secrets.token_hex(32)
    key_path.write_text(secret, encoding="utf-8")
    try:
        key_path.chmod(0o600)
    except OSError:
        pass
    return secret


def _runtime_environment(test_config=None):
    configured = os.environ.get("CAMPUSMATE_ENV")
    if configured is None:
        if test_config and test_config.get("TESTING"):
            return "testing"
        raise RuntimeError(
            "Set CAMPUSMATE_ENV to 'development' or 'production' before starting CampusMate."
        )
    environment = configured.strip().lower()
    if environment not in {"development", "production"}:
        raise RuntimeError(
            "Invalid CAMPUSMATE_ENV. Use exactly 'development' or 'production' (case-insensitive)."
        )
    return environment


def create_app(test_config=None, *, initialize_db=False):
    environment = _runtime_environment(test_config)
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        DATABASE=os.environ.get("CAMPUSMATE_DATABASE", str(DATABASE_PATH)),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=environment == "production",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 14,
        DEBUG=environment == "development",
        CAMPUSMATE_ENV=environment,
    )
    if test_config:
        app.config.update(test_config)
    # Keep security-sensitive modes authoritative even if a caller supplied overrides.
    app.config["CAMPUSMATE_ENV"] = environment
    app.config["DEBUG"] = environment == "development"
    app.config["SESSION_COOKIE_SECURE"] = environment == "production"

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    configured_secret = os.environ.get("CAMPUSMATE_SECRET_KEY")
    if configured_secret:
        app.config["SECRET_KEY"] = configured_secret
    elif environment == "production" and not app.config.get("SECRET_KEY"):
        raise RuntimeError("Set CAMPUSMATE_SECRET_KEY before starting in production.")
    elif not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = _development_secret(app.instance_path)

    csrf.init_app(app)
    app.extensions["dummy_login_password_hash"] = generate_password_hash(secrets.token_urlsafe(32))
    app.extensions["login_limiter"] = InMemoryLoginLimiter(
        app.config["SECRET_KEY"],
        limit=app.config.get("LOGIN_RATE_LIMIT", 5),
        window_seconds=app.config.get("LOGIN_RATE_WINDOW_SECONDS", 60),
        max_keys=app.config.get("LOGIN_RATE_MAX_KEYS", 10000),
    )
    if initialize_db:
        initialize_database(app.config["DATABASE"])

    @app.cli.command("init-db")
    def init_db_command():
        """Explicitly create the schema and seed any missing reference data."""
        initialize_database(app.config["DATABASE"])
        print(f"CampusMate database initialized: {app.config['DATABASE']}")

    @app.before_request
    def load_current_user():
        user_id = session.get("user_id")
        g.current_user = None
        if user_id is None:
            return
        with get_connection(app.config["DATABASE"]) as db:
            g.current_user = db.execute(
                "SELECT id, email, display_name, created_at FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        if g.current_user is None:
            session.clear()

    @app.context_processor
    def inject_user():
        return {"current_user": getattr(g, "current_user", None)}

    def login_required(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if getattr(g, "current_user", None) is None:
                flash("請先登入才能使用此功能。", "info")
                requested_return = (
                    request.form.get("next", "") if request.method == "POST"
                    else request.full_path.rstrip("?")
                )
                return redirect(url_for(
                    "login", next=_safe_local_path(requested_return, url_for("profile"))
                ))
            return view(*args, **kwargs)
        return wrapped_view

    @app.route("/")
    def home():
        user_id = g.current_user["id"] if getattr(g, "current_user", None) else None
        categories, items = get_homepage_data(app.config["DATABASE"], user_id=user_id)
        return render_template(
            "index.html", categories=categories, items=items,
            logged_out=request.args.get("logged_out") == "1",
        )

    @app.route("/explore")
    def explore():
        query = request.args.get("q", "").strip()[:200]
        category_slug = request.args.get("category", "").strip()
        sort = request.args.get("sort", "recent")
        if sort not in {"recent", "important"}:
            sort = "recent"

        with get_connection(app.config["DATABASE"]) as db:
            categories = db.execute(
                "SELECT slug, name, icon FROM categories ORDER BY id"
            ).fetchall()
        category_map = {category["slug"]: category for category in categories}
        if category_slug and category_slug not in category_map:
            abort(404)

        items = search_public_items(
            app.config["DATABASE"], query=query,
            category_slug=category_slug, sort=sort,
            user_id=g.current_user["id"] if getattr(g, "current_user", None) else None,
        )
        return render_template(
            "explore.html", query=query, categories=categories,
            selected_category=category_map.get(category_slug),
            category_slug=category_slug, sort=sort, items=items,
        )

    @app.route("/items/<int:item_id>")
    def item_detail(item_id):
        user_id = g.current_user["id"] if getattr(g, "current_user", None) else None
        item = get_item_for_view(app.config["DATABASE"], item_id, user_id)
        if item is None:
            abort(404)
        safe_source_url = ""
        if item["source_url"]:
            try:
                parsed_source = urlsplit(item["source_url"])
                if parsed_source.scheme in {"http", "https"} and parsed_source.hostname:
                    # Accessing .port can raise ValueError for malformed port values.
                    parsed_source.port
                    safe_source_url = item["source_url"]
            except ValueError:
                safe_source_url = ""
        return render_template(
            "item_detail.html", item=item,
            back_url=request.args.get("next", ""),
            safe_source_url=safe_source_url,
        )

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("404.html"), 404

    @app.route("/favorites")
    @login_required
    def favorites():
        items = get_user_favorites(app.config["DATABASE"], g.current_user["id"])
        return render_template("favorites.html", items=items)

    @app.route("/favorites/<int:item_id>/add", methods=["POST"])
    @login_required
    def add_favorite(item_id):
        user_id = g.current_user["id"]
        return_to = _safe_local_path(request.form.get("next", ""), url_for("favorites"))
        with get_connection(app.config["DATABASE"]) as db:
            public_item = db.execute(
                "SELECT id FROM items WHERE id = ? AND is_public = 1", (item_id,)
            ).fetchone()
            if public_item is None:
                flash("這筆公開資訊不存在或目前無法收藏。", "error")
            else:
                cursor = db.execute(
                    """INSERT INTO favorites (user_id, item_id) VALUES (?, ?)
                       ON CONFLICT(user_id, item_id) DO NOTHING""",
                    (user_id, item_id),
                )
                flash(
                    "已加入我的收藏。" if cursor.rowcount else "這筆資訊已在你的收藏清單中。",
                    "success" if cursor.rowcount else "info",
                )
        return redirect(return_to)

    @app.route("/favorites/<int:item_id>/remove", methods=["POST"])
    @login_required
    def remove_favorite(item_id):
        user_id = g.current_user["id"]
        return_to = _safe_local_path(request.form.get("next", ""), url_for("favorites"))
        with get_connection(app.config["DATABASE"]) as db:
            cursor = db.execute(
                """DELETE FROM favorites
                   WHERE user_id = ? AND item_id = ?
                     AND EXISTS (SELECT 1 FROM items WHERE id = ? AND is_public = 1)""",
                (user_id, item_id, item_id),
            )
        if cursor.rowcount:
            flash("已從我的收藏移除。", "success")
        else:
            flash("找不到這筆收藏，或它已經被移除。", "info")
        return redirect(return_to)

    @app.route("/my-items")
    @login_required
    def my_items():
        with get_connection(app.config["DATABASE"]) as db:
            items = db.execute(
                """SELECT items.id, items.title, items.summary, items.important_date,
                          items.created_at, items.updated_at, items.is_demo,
                          categories.slug AS category_slug, categories.name AS category
                   FROM items JOIN categories ON categories.id = items.category_id
                   WHERE items.created_by = ? AND items.is_public = 0
                   ORDER BY items.created_at DESC, items.id DESC""",
                (g.current_user["id"],),
            ).fetchall()
        return render_template("my_items.html", items=items)

    @app.route("/my-items/new", methods=["GET", "POST"])
    @login_required
    def new_item():
        with get_connection(app.config["DATABASE"]) as db:
            categories = db.execute("SELECT id, name FROM categories ORDER BY id").fetchall()
        blank_item = {
            "title": "", "category_id": "", "summary": "", "content": "",
            "source_url": "", "important_date": "",
        }
        submission_key = secrets.token_urlsafe(32)
        if request.method == "POST":
            submission_key = request.form.get("submission_key", "").strip()
            if not SUBMISSION_KEY_PATTERN.fullmatch(submission_key):
                flash("表單識別資訊已失效，請重新確認後送出。", "error")
                submission_key = secrets.token_urlsafe(32)
                return render_template(
                    "item_form.html", item=request.form, categories=categories,
                    mode="new", submission_key=submission_key,
                ), 400

            item_data, errors = _parse_private_item_form(request.form, categories)
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_template(
                    "item_form.html", item=item_data, categories=categories,
                    mode="new", submission_key=submission_key,
                ), 400

            user_id = g.current_user["id"]
            replayed = False
            try:
                with get_connection(app.config["DATABASE"]) as db:
                    # Serialize the lookup + insert so concurrent retries see one committed key.
                    db.execute("BEGIN IMMEDIATE")
                    previous = db.execute(
                        """SELECT item_id FROM item_submissions
                           WHERE user_id = ? AND submission_key = ?""",
                        (user_id, submission_key),
                    ).fetchone()
                    if previous is not None:
                        item_id = previous["item_id"]
                        replayed = True
                    else:
                        cursor = db.execute(
                            """INSERT INTO items
                               (title, summary, content, category_id, source_url, important_date,
                                created_by, is_public, is_demo)
                               VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)""",
                            (item_data["title"], item_data["summary"], item_data["content"],
                             item_data["category_id"], item_data["source_url"], item_data["important_date"],
                             user_id),
                        )
                        item_id = cursor.lastrowid
                        db.execute(
                            """INSERT INTO item_submissions (user_id, submission_key, item_id)
                               VALUES (?, ?, ?)""",
                            (user_id, submission_key, item_id),
                        )
            except sqlite3.Error:
                flash("儲存未完成，資料尚未建立。請稍後重試。", "error")
                return render_template(
                    "item_form.html", item=item_data, categories=categories,
                    mode="new", submission_key=submission_key,
                ), 503

            if replayed and item_id is None:
                flash("這筆提交先前已處理，但資訊已刪除。請重新開啟新增表單。", "info")
                return redirect(url_for("my_items"))
            flash(
                "這筆資訊已儲存，沒有重複建立。" if replayed else "私人資訊已新增。",
                "info" if replayed else "success",
            )
            return redirect(url_for("item_detail", item_id=item_id, next=url_for("my_items")))
        return render_template(
            "item_form.html", item=blank_item, categories=categories,
            mode="new", submission_key=submission_key,
        )

    @app.route("/my-items/<int:item_id>/edit", methods=["GET", "POST"])
    @login_required
    def edit_item(item_id):
        user_id = g.current_user["id"]
        with get_connection(app.config["DATABASE"]) as db:
            item = db.execute(
                """SELECT id, title, summary, content, category_id, source_url, important_date
                   FROM items WHERE id = ? AND created_by = ? AND is_public = 0""",
                (item_id, user_id),
            ).fetchone()
            categories = db.execute("SELECT id, name FROM categories ORDER BY id").fetchall()
        if item is None:
            abort(404)
        if request.method == "POST":
            item_data, errors = _parse_private_item_form(request.form, categories)
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_template("item_form.html", item=item_data, categories=categories, mode="edit"), 400
            with get_connection(app.config["DATABASE"]) as db:
                cursor = db.execute(
                    """UPDATE items
                       SET title = ?, summary = ?, content = ?, category_id = ?,
                           source_url = ?, important_date = ?,
                           updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                       WHERE id = ? AND created_by = ? AND is_public = 0""",
                    (item_data["title"], item_data["summary"], item_data["content"],
                     item_data["category_id"], item_data["source_url"], item_data["important_date"],
                     item_id, user_id),
                )
            if cursor.rowcount != 1:
                abort(404)
            flash("私人資訊已更新。", "success")
            return redirect(url_for("item_detail", item_id=item_id, next=url_for("my_items")))
        return render_template("item_form.html", item=item, categories=categories, mode="edit")

    @app.route("/my-items/<int:item_id>/delete", methods=["GET", "POST"])
    @login_required
    def delete_item(item_id):
        user_id = g.current_user["id"]
        with get_connection(app.config["DATABASE"]) as db:
            item = db.execute(
                "SELECT id, title, summary FROM items WHERE id = ? AND created_by = ? AND is_public = 0",
                (item_id, user_id),
            ).fetchone()
        if item is None:
            abort(404)
        if request.method == "POST":
            with get_connection(app.config["DATABASE"]) as db:
                cursor = db.execute(
                    "DELETE FROM items WHERE id = ? AND created_by = ? AND is_public = 0",
                    (item_id, user_id),
                )
            if cursor.rowcount != 1:
                abort(404)
            flash("私人資訊已刪除。", "success")
            return redirect(url_for("my_items"))
        return render_template("confirm_delete.html", item=item)

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if getattr(g, "current_user", None):
            return redirect(url_for("profile"))
        if request.method == "POST":
            display_name = request.form.get("display_name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            password_confirm = request.form.get("password_confirm", "")

            error = None
            if not display_name or not email or not password or not password_confirm:
                error = "請填寫所有欄位。"
            elif len(display_name) > 50:
                error = "顯示名稱請勿超過 50 個字元。"
            elif len(email) > 254 or not EMAIL_PATTERN.fullmatch(email):
                error = "請輸入有效的 Email 格式。"
            elif len(password) < MIN_PASSWORD_LENGTH:
                error = f"密碼至少需要 {MIN_PASSWORD_LENGTH} 個字元。"
            elif len(password) > MAX_PASSWORD_LENGTH:
                error = f"密碼不可超過 {MAX_PASSWORD_LENGTH} 個字元。"
            elif password != password_confirm:
                error = "密碼與確認密碼不一致。"

            if error:
                flash(error, "error")
                return render_template("register.html", display_name=display_name, email=email), 400

            try:
                with get_connection(app.config["DATABASE"]) as db:
                    db.execute(
                        "INSERT INTO users (email, display_name, password_hash) VALUES (?, ?, ?)",
                        (email, display_name, generate_password_hash(password)),
                    )
            except sqlite3.IntegrityError:
                # Keep the response generic so account existence is not exposed in login flows.
                flash("此 Email 無法用於註冊，請確認資料或改用其他 Email。", "error")
                return render_template("register.html", display_name=display_name, email=email), 400

            flash("註冊成功，請使用帳號登入。", "success")
            return redirect(url_for("login"))
        return render_template("register.html", display_name="", email="")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if getattr(g, "current_user", None):
            return redirect(url_for("profile"))
        next_url = request.args.get("next", "")
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            limiter = app.extensions["login_limiter"]
            if limiter.is_limited(request.remote_addr, email):
                flash("登入嘗試次數過多，請稍後再試。", "error")
                return render_template("login.html", email=email, next_url=request.form.get("next", "")), 429
            with get_connection(app.config["DATABASE"]) as db:
                user = db.execute(
                    "SELECT id, email, display_name, password_hash FROM users WHERE email = ?",
                    (email,),
                ).fetchone()
            if user is None:
                # Match the password-hash work for unknown accounts to reduce timing clues.
                check_password_hash(app.extensions["dummy_login_password_hash"], password)
                valid_credentials = False
            else:
                valid_credentials = check_password_hash(user["password_hash"], password)
            if not valid_credentials:
                limiter.record_failure(request.remote_addr, email)
                flash("Email 或密碼不正確，請再試一次。", "error")
                return render_template("login.html", email=email, next_url=request.form.get("next", "")), 401

            limiter.clear(request.remote_addr, email)
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = True
            destination = _safe_local_path(request.form.get("next", ""), url_for("profile"))
            return redirect(destination)
        return render_template("login.html", email="", next_url=next_url)

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("home", logged_out=1))

    @app.route("/me")
    @login_required
    def profile():
        return render_template("profile.html")

    return app


if __name__ == "__main__":
    app = create_app(initialize_db=True)
    app.run(debug=app.config["DEBUG"])
