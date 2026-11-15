"""SQLite schema, initialization, and small query helpers for CampusMate."""

from pathlib import Path
import sqlite3
from datetime import date, datetime
import shutil
from contextlib import contextmanager
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


BASE_DIR = Path(__file__).resolve().parent
DATABASE_DIR = BASE_DIR / "instance"
DATABASE_PATH = DATABASE_DIR / "campusmate.sqlite3"

CATEGORY_SEEDS = [
    ("scholarship", "獎學金", "✦", "補助、獎學金與申請資訊"),
    ("courses", "課程學習", "⌁", "選課資源與學習支持"),
    ("events", "校園活動", "◉", "講座、展演與社團活動"),
    ("announcements", "行政公告", "▤", "校務通知與重要提醒"),
    ("life-services", "生活服務", "⌂", "交通、住宿與日常服務"),
]

# Stable keys make seeding repeatable. These examples are explicitly marked as demos.
ITEM_SEEDS = [
    (
        "demo-scholarship", "校內清寒助學金申請提醒",
        "整理申請資格、應備文件與校內承辦單位，實際期限請以官方公告為準。",
        "本筆為 CampusMate 測試示範內容，非正式校方公告。申請資格、文件及期限請以學校官方資訊為準。",
        "scholarship", "https://osa.ntu.edu.tw/", None, "獎助資訊", "✦", "mint",
    ),
    (
        "demo-campus-events", "校園講座與藝文活動去哪裡找？",
        "快速認識校內活動資訊的常見發布管道，發現課堂之外的精彩日常。",
        "本筆為 CampusMate 測試示範內容，非正式活動公告。活動資訊請以學校官方網站公告為準。",
        "events", "https://www.ntu.edu.tw/", None, "近期活動", "◉", "peach",
    ),
    (
        "demo-student-life", "新生校園生活服務小整理",
        "從校園交通到常用服務，先收藏這份生活資訊索引，讓日常更順手。",
        "本筆為 CampusMate 測試示範內容，提供校園生活資訊索引的展示用途。服務細節請參考學校官方網站。",
        "life-services", "https://www.ntu.edu.tw/", None, "生活指南", "⌂", "blue",
    ),
    (
        "demo-course-planning", "選課與課程規劃資訊整理",
        "整理選課前可先確認的課程查詢與修課規定入口。此卡片為測試示範內容。",
        "本筆為 CampusMate 測試示範內容，並非正式選課公告。選課時程與課程規定請以臺大教務處及課程網官方資訊為準。",
        "courses", "https://www.aca.ntu.edu.tw/", None, "課程資源", "⌁", "blue",
    ),
    (
        "demo-admin-notice", "校務行政公告查詢入口",
        "快速找到校內行政單位公告入口，重要校務資訊請以官方發布為準。",
        "本筆為 CampusMate 測試示範內容，並非正式校方公告。最新行政通知請前往臺灣大學官方網站查閱。",
        "announcements", "https://www.ntu.edu.tw/", None, "行政公告", "▤", "mint",
    ),
]

# Stable keys make official resource imports repeatable. These entries describe
# long-lived NTU information portals, not individual notices or deadlines.
OFFICIAL_ITEM_SEEDS = [
    (
        "official-ntu-financial-support",
        "臺大經濟支持與獎助學金入口",
        "查找臺大學務處提供的助學措施與獎助學金資訊；各項資格及申請時間請以原公告為準。",
        "臺大學務處經濟支持頁面彙整助學措施與獎助學金相關入口。不同方案可能有不同適用對象及辦理時間，請開啟官方頁面查看最新說明，不要以本入口卡片取代原公告。",
        "scholarship", "https://osa.ntu.edu.tw/services/financialaid", "2026-11-11", 1,
    ),
    (
        "official-ntu-learning-resources",
        "臺大學習資源入口",
        "由臺大校園資訊頁連結數位學習、學習諮詢、演講、開放式課程、NTU COOL 與圖書館服務。",
        "臺大校園資訊頁整理多種學習資源入口，包括數位學習、學習諮詢、臺大演講網、臺大開放式課程、NTU COOL、圖書館服務及計中資訊應用課程。各服務內容請以其官方網站為準。",
        "courses", "https://www.ntu.edu.tw/campus/campus.html", "2026-11-11", 1,
    ),
    (
        "official-ntu-activity-center-board",
        "臺大學生活動中心公告入口",
        "瀏覽學生活動中心發布的公告與場地相關資訊；個別活動及申請狀態請以原公告為準。",
        "臺大學生活動中心管理組公告頁提供活動中心公告資訊。此筆是公告入口，不代表任何單一活動仍在舉辦或受理；日期、對象及辦理狀態請以各公告內容為準。",
        "events", "https://www.active.ntu.edu.tw/board", "2026-11-11", 1,
    ),
    (
        "official-ntu-campus-announcements",
        "臺大校園公告系統",
        "查詢校內公告的類別、發布單位、公告日期與截止日期；請確認公告對象及期限。",
        "臺大校園公告系統提供公告類別、單位、公告主旨、公告日期及截止日期等資訊。公告涵蓋不同單位與對象，請查看原文確認是否適用及是否仍有效。",
        "announcements", "https://ann.cc.ntu.edu.tw/asp/deptnews.asp", "2026-11-11", 1,
    ),
    (
        "official-ntu-campus-life-services",
        "臺大校園生活服務入口",
        "由臺大校園資訊頁連結住宿、社團活動、保健中心、交通與停車等生活服務。",
        "臺大校園資訊頁提供多項生活服務入口，例如學生住宿、社團活動資訊、保健中心門診、交通與停車、緊急聯絡及場館資訊。實際服務內容請以承辦單位的官方頁面為準。",
        "life-services", "https://www.ntu.edu.tw/campus/campus.html", "2026-11-11", 1,
    ),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL UNIQUE,
    icon TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    seed_key TEXT UNIQUE,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    content TEXT NOT NULL,
    category_id INTEGER NOT NULL,
    source_url TEXT,
    important_date TEXT,
    last_verified_at TEXT CHECK (
        last_verified_at IS NULL OR (
            length(last_verified_at) = 10
            AND last_verified_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
            AND date(last_verified_at) = last_verified_at
        )
    ),
    is_current INTEGER DEFAULT NULL CHECK (is_current IS NULL OR is_current IN (0, 1)),
    created_by INTEGER,
    is_public INTEGER NOT NULL DEFAULT 0 CHECK (is_public IN (0, 1)),
    is_demo INTEGER NOT NULL DEFAULT 0 CHECK (is_demo IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (category_id) REFERENCES categories(id) ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (created_by) REFERENCES users(id) ON UPDATE CASCADE ON DELETE CASCADE,
    CHECK ((is_public = 1 AND created_by IS NULL) OR (is_public = 0 AND created_by IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS favorites (
    user_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (user_id, item_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON UPDATE CASCADE ON DELETE CASCADE,
    FOREIGN KEY (item_id) REFERENCES items(id) ON UPDATE CASCADE ON DELETE CASCADE
);

-- Records create-form submissions so a retried POST cannot create a second item.
-- The key is scoped to a member; null item_id keeps a short-lived tombstone after deletion.
CREATE TABLE IF NOT EXISTS item_submissions (
    user_id INTEGER NOT NULL,
    submission_key TEXT NOT NULL,
    item_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (user_id, submission_key),
    CHECK (length(submission_key) BETWEEN 32 AND 128),
    FOREIGN KEY (user_id) REFERENCES users(id) ON UPDATE CASCADE ON DELETE CASCADE,
    FOREIGN KEY (item_id) REFERENCES items(id) ON UPDATE CASCADE ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_items_category_public ON items(category_id, is_public);
CREATE INDEX IF NOT EXISTS idx_items_created_by ON items(created_by);
CREATE INDEX IF NOT EXISTS idx_favorites_item_id ON favorites(item_id);

CREATE TRIGGER IF NOT EXISTS trg_items_updated_at
AFTER UPDATE ON items
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE items
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = NEW.id;
END;
"""


def _connect(database_path):
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def get_connection(database_path=DATABASE_PATH):
    """Yield a row-aware connection, committing or rolling back and always closing it."""
    connection = _connect(database_path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def migrate_item_owner_delete_rule(database_path=DATABASE_PATH):
    """Change item owner deletion to CASCADE while preserving every existing row."""
    database_path = Path(database_path)
    if not database_path.exists():
        return

    with get_connection(database_path) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
        if "items" not in tables:
            return
        owner_fk = next(
            (row for row in connection.execute("PRAGMA foreign_key_list(items)") if row["from"] == "created_by"),
            None,
        )
        if owner_fk is None or owner_fk["on_delete"].upper() != "SET NULL":
            return
        existing_columns = {row["name"] for row in connection.execute("PRAGMA table_info(items)")}

    # Keep a SQLite-consistent backup beside the database before changing its schema.
    backup_path = database_path.with_name(
        f"{database_path.stem}-pre-owner-migration-{datetime.now().strftime('%Y%m%d-%H%M%S')}{database_path.suffix}"
    )
    source = sqlite3.connect(database_path)
    backup = sqlite3.connect(backup_path)
    try:
        source.backup(backup)
    finally:
        source.close()
        backup.close()
    shutil.copystat(database_path, backup_path)

    connection = _connect(database_path)
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("""CREATE TABLE items_new (
            id INTEGER PRIMARY KEY,
            seed_key TEXT UNIQUE,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            content TEXT NOT NULL,
            category_id INTEGER NOT NULL,
            source_url TEXT,
            important_date TEXT,
            last_verified_at TEXT CHECK (
                last_verified_at IS NULL OR (
                    length(last_verified_at) = 10
                    AND last_verified_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
                    AND date(last_verified_at) = last_verified_at
                )
            ),
            is_current INTEGER DEFAULT NULL CHECK (is_current IS NULL OR is_current IN (0, 1)),
            created_by INTEGER,
            is_public INTEGER NOT NULL DEFAULT 0 CHECK (is_public IN (0, 1)),
            is_demo INTEGER NOT NULL DEFAULT 0 CHECK (is_demo IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            FOREIGN KEY (category_id) REFERENCES categories(id) ON UPDATE CASCADE ON DELETE RESTRICT,
            FOREIGN KEY (created_by) REFERENCES users(id) ON UPDATE CASCADE ON DELETE CASCADE,
            CHECK ((is_public = 1 AND created_by IS NULL) OR (is_public = 0 AND created_by IS NOT NULL))
        )""")
        verified_expr = "last_verified_at" if "last_verified_at" in existing_columns else "NULL"
        current_expr = "is_current" if "is_current" in existing_columns else "NULL"
        connection.execute(f"""INSERT INTO items_new
            (id, seed_key, title, summary, content, category_id, source_url,
             important_date, last_verified_at, is_current, created_by, is_public, is_demo,
             created_at, updated_at)
            SELECT id, seed_key, title, summary, content, category_id, source_url,
                   important_date,
                   {verified_expr}, {current_expr},
                   created_by, is_public, is_demo, created_at, updated_at
            FROM items""")
        connection.execute("DROP TABLE items")
        connection.execute("ALTER TABLE items_new RENAME TO items")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_items_category_public ON items(category_id, is_public)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_items_created_by ON items(created_by)")
        connection.execute("""CREATE TRIGGER IF NOT EXISTS trg_items_updated_at
            AFTER UPDATE ON items FOR EACH ROW
            WHEN NEW.updated_at = OLD.updated_at
            BEGIN
                UPDATE items SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = NEW.id;
            END""")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(f"Foreign key violations after migration: {violations}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.close()


def migrate_public_item_metadata(database_path=DATABASE_PATH):
    """Add verification state without rebuilding rows, and clear the old demo date marker."""
    database_path = Path(database_path)
    if not database_path.exists():
        return

    with get_connection(database_path) as connection:
        tables = {row["name"] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
        if "items" not in tables:
            return
        connection.execute("BEGIN IMMEDIATE")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(items)")}
        if "last_verified_at" not in columns:
            connection.execute(
                "ALTER TABLE items ADD COLUMN last_verified_at TEXT CHECK ("
                "last_verified_at IS NULL OR (length(last_verified_at) = 10 "
                "AND last_verified_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' "
                "AND date(last_verified_at) = last_verified_at))"
            )
        if "is_current" not in columns:
            connection.execute(
                "ALTER TABLE items ADD COLUMN is_current INTEGER DEFAULT NULL "
                "CHECK (is_current IS NULL OR is_current IN (0, 1))"
            )
        connection.execute(
            "UPDATE items SET important_date = NULL "
            "WHERE is_demo = 1 AND important_date = ?",
            ("示範資料",),
        )


def taipei_today():
    """Return the current calendar date in Taiwan, independent of server timezone."""
    return datetime.now(ZoneInfo("Asia/Taipei")).date()


def _present_item(row, today=None):
    """Add display-only date, source, and status labels to an item row."""
    item = dict(row)
    today = today or taipei_today()
    due_value = item.get("important_date")
    item["date_status"] = "none"
    item["important_date_display"] = None
    if due_value:
        try:
            due_date = date.fromisoformat(due_value)
            item["important_date_display"] = due_date.isoformat()
            item["date_status"] = "past" if due_date < today else "upcoming"
        except (TypeError, ValueError):
            # Invalid legacy date text is treated as missing, never as a real deadline.
            item["date_status"] = "none"

    item["current_label"] = {
        1: "已確認仍適用",
        0: "已確認不再適用",
        None: "尚未確認",
    }.get(item.get("is_current"), "尚未確認")
    item["source_label"] = ""
    item["source_is_official"] = False
    if item.get("source_url"):
        try:
            parsed = urlsplit(item["source_url"])
            if parsed.scheme in {"http", "https"} and parsed.hostname:
                parsed.port  # Reject malformed ports consistently with the detail route.
                hostname = parsed.hostname.lower()
                item["source_label"] = hostname.removeprefix("www.")
                item["source_is_official"] = (
                    hostname == "ntu.edu.tw" or hostname.endswith(".ntu.edu.tw")
                )
            else:
                item["source_label"] = "來源連結目前無法使用"
        except ValueError:
            item["source_label"] = "來源連結目前無法使用"
    else:
        item["source_label"] = "尚未提供來源"
    return item


def initialize_database(database_path=DATABASE_PATH):
    """Create schema and insert missing seed rows without resetting existing data."""
    database_path = Path(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    migrate_item_owner_delete_rule(database_path)
    migrate_public_item_metadata(database_path)

    with get_connection(database_path) as connection:
        connection.executescript(SCHEMA)
        for slug, name, icon, description in CATEGORY_SEEDS:
            connection.execute(
                """INSERT INTO categories (slug, name, icon, description)
                   VALUES (?, ?, ?, ?) ON CONFLICT(slug) DO NOTHING""",
                (slug, name, icon, description),
            )

        for (seed_key, title, summary, content, category_slug, source_url,
             important_date, _tag, _symbol, _color) in ITEM_SEEDS:
            connection.execute(
                """INSERT INTO items
                   (seed_key, title, summary, content, category_id, source_url,
                    important_date, created_by, is_public, is_demo)
                   SELECT ?, ?, ?, ?, categories.id, ?, ?, NULL, 1, 1
                   FROM categories WHERE categories.slug = ?
                   ON CONFLICT(seed_key) DO NOTHING""",
                (seed_key, title, summary, content, source_url, important_date, category_slug),
            )

        for (seed_key, title, summary, content, category_slug, source_url,
             last_verified_at, is_current) in OFFICIAL_ITEM_SEEDS:
            connection.execute(
                """INSERT INTO items
                   (seed_key, title, summary, content, category_id, source_url,
                    important_date, last_verified_at, is_current, created_by,
                    is_public, is_demo)
                   SELECT ?, ?, ?, ?, categories.id, ?, NULL, ?, ?, NULL, 1, 0
                   FROM categories WHERE categories.slug = ?
                   ON CONFLICT(seed_key) DO NOTHING""",
                (seed_key, title, summary, content, source_url,
                 last_verified_at, is_current, category_slug),
            )


def get_homepage_data(database_path=DATABASE_PATH, user_id=None):
    """Return public categories and public homepage items in template-ready form."""
    with get_connection(database_path) as connection:
        categories = connection.execute(
            "SELECT slug, name, icon, description FROM categories ORDER BY id"
        ).fetchall()
        rows = connection.execute(
            """SELECT items.id, items.seed_key, categories.slug AS category_slug,
                      categories.name AS category, items.title,
                      items.summary AS description, items.source_url,
                      items.important_date, items.last_verified_at, items.is_current,
                      items.created_at, items.is_demo,
                      EXISTS (SELECT 1 FROM favorites
                              WHERE favorites.item_id = items.id AND favorites.user_id = ?) AS is_favorited
               FROM items JOIN categories ON categories.id = items.category_id
               WHERE items.is_public = 1
               ORDER BY items.is_demo ASC, items.created_at DESC, items.id DESC LIMIT 6""",
            (user_id,),
        ).fetchall()

    appearance = {
        "demo-scholarship": ("獎助資訊", "✦", "mint"),
        "demo-campus-events": ("近期活動", "◉", "peach"),
        "demo-student-life": ("生活指南", "⌂", "blue"),
        "demo-course-planning": ("課程資源", "⌁", "blue"),
        "demo-admin-notice": ("行政公告", "▤", "mint"),
    }
    items = []
    for row in rows:
        tag, symbol, color = appearance.get(row["seed_key"], (row["category"], "✦", "mint"))
        items.append(_present_item({
            "id": row["id"], "category_slug": row["category_slug"],
            "category": row["category"], "tag": tag, "title": row["title"],
            "description": row["description"], "summary": row["description"],
            "source_url": row["source_url"],
            "date": row["important_date"],
            "important_date": row["important_date"], "created_at": row["created_at"],
            "last_verified_at": row["last_verified_at"], "is_current": row["is_current"],
            "is_demo": row["is_demo"], "is_favorited": row["is_favorited"],
            "symbol": symbol, "color": color,
        }))
    return categories, items


def search_public_items(database_path=DATABASE_PATH, query="", category_slug="", sort="recent", user_id=None):
    """Search only public items with bound parameters and an allow-listed sort order."""
    clauses = ["items.is_public = 1"]
    parameters = []
    query = (query or "").strip()
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        clauses.append("(items.title LIKE ? ESCAPE '\\' OR items.summary LIKE ? ESCAPE '\\' OR items.content LIKE ? ESCAPE '\\' OR categories.name LIKE ? ESCAPE '\\')")
        parameters.extend((pattern, pattern, pattern, pattern))
    if category_slug:
        clauses.append("categories.slug = ?")
        parameters.append(category_slug)

    order_by = {
        "recent": "items.created_at DESC, items.id DESC",
        "important": """CASE WHEN date(items.important_date) >= ? THEN 0
            WHEN date(items.important_date) < ? THEN 1 ELSE 2 END,
            CASE WHEN date(items.important_date) >= ? THEN date(items.important_date) END ASC,
            CASE WHEN date(items.important_date) < ? THEN date(items.important_date) END DESC,
            items.created_at DESC, items.id DESC""",
    }.get(sort, "items.created_at DESC, items.id DESC")
    sql = f"""SELECT items.id, items.seed_key, items.title, items.summary,
                     items.content, items.source_url, items.important_date,
                     items.last_verified_at, items.is_current,
                     items.created_at, items.updated_at, items.is_demo,
                     EXISTS (SELECT 1 FROM favorites
                             WHERE favorites.item_id = items.id AND favorites.user_id = ?) AS is_favorited,
                     categories.slug AS category_slug, categories.name AS category
              FROM items JOIN categories ON categories.id = items.category_id
              WHERE {' AND '.join(clauses)}
              ORDER BY {order_by}"""
    query_parameters = [user_id, *parameters]
    if sort == "important":
        taipei_date = taipei_today().isoformat()
        query_parameters.extend([taipei_date] * 4)
    with get_connection(database_path) as connection:
        return [_present_item(row) for row in connection.execute(sql, query_parameters).fetchall()]


def get_item_for_view(database_path, item_id, user_id=None):
    """Return a public item or the requesting user's own private item."""
    with get_connection(database_path) as connection:
        row = connection.execute(
            """SELECT items.id, items.seed_key, items.title, items.summary,
                      items.content, items.source_url, items.important_date,
                      items.last_verified_at, items.is_current,
                      items.created_at, items.updated_at, items.is_public, items.is_demo,
                      EXISTS (SELECT 1 FROM favorites
                              WHERE favorites.item_id = items.id AND favorites.user_id = ?) AS is_favorited,
                      categories.slug AS category_slug, categories.name AS category
               FROM items JOIN categories ON categories.id = items.category_id
               WHERE items.id = ? AND (items.is_public = 1 OR
                     (items.is_public = 0 AND items.created_by = ?))""",
            (user_id, item_id, user_id),
        ).fetchone()
    return _present_item(row) if row else None


def get_user_favorites(database_path, user_id):
    """Return only the current member's saved public items."""
    with get_connection(database_path) as connection:
        rows = connection.execute(
            """SELECT items.id, items.seed_key, items.title, items.summary,
                      items.content, items.source_url, items.important_date,
                      items.last_verified_at, items.is_current,
                      items.created_at, items.updated_at, items.is_demo,
                      1 AS is_favorited, categories.slug AS category_slug,
                      categories.name AS category, favorites.created_at AS favorited_at
               FROM favorites
               JOIN items ON items.id = favorites.item_id
               JOIN categories ON categories.id = items.category_id
               WHERE favorites.user_id = ? AND items.is_public = 1
               ORDER BY favorites.created_at DESC, favorites.item_id DESC""",
            (user_id,),
        ).fetchall()
    return [_present_item(row) for row in rows]


if __name__ == "__main__":
    initialize_database()
    print(f"CampusMate database initialized: {DATABASE_PATH}")
