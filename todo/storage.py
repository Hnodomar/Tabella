"""SQLite persistence; independent of GTK and third-party packages."""

from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3


STATUSES = ("not done", "in progress", "done")


def database_path():
    data_home = os.environ.get("XDG_DATA_HOME")
    root = Path(data_home) if data_home and Path(data_home).is_absolute() else Path.home() / ".local/share"
    return root / "simple-todo" / "tasks.sqlite3"


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else database_path()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=1)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA journal_mode = WAL")
            self.db.execute("PRAGMA synchronous = FULL")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise RuntimeError("This task database was created by a newer version of the app.")
            with self.db:
                self.db.execute("""
                    CREATE TABLE IF NOT EXISTS tasks (
                        id INTEGER PRIMARY KEY,
                        title TEXT NOT NULL CHECK(length(trim(title)) > 0),
                        status INTEGER NOT NULL DEFAULT 0 CHECK(status BETWEEN 0 AND 2),
                        created_at TEXT NOT NULL,
                        body TEXT NOT NULL DEFAULT ''
                    )
                """)
                self.db.execute("""
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value INTEGER NOT NULL CHECK(value IN (0, 1))
                    )
                """)
                self.db.execute("""
                    CREATE TABLE IF NOT EXISTS app_preferences (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                            CHECK(value IN ('system', 'light', 'dark'))
                    )
                """)
                self.db.execute("""
                    CREATE TABLE IF NOT EXISTS preferences (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                """)
                old_theme = self.db.execute(
                    "SELECT value FROM app_preferences WHERE key = 'theme'"
                ).fetchone()
                old_light = self.db.execute(
                    "SELECT value FROM settings WHERE key = 'light_mode'"
                ).fetchone()
                internal_theme = self.db.execute(
                    "SELECT value FROM preferences WHERE key = 'internal_theme'"
                ).fetchone()
                if internal_theme is None:
                    mode = old_theme['value'] if old_theme and old_theme['value'] in ('light', 'dark') else 'light'
                    if old_light and old_light['value'] and old_theme is None:
                        mode = 'light'
                    self.db.execute(
                        "INSERT INTO preferences (key, value) VALUES ('internal_theme', ?)",
                        (mode,),
                    )
                ubuntu_theme = self.db.execute(
                    "SELECT value FROM preferences WHERE key = 'ubuntu_theme'"
                ).fetchone()
                if ubuntu_theme is None:
                    use_ubuntu = old_theme is None or old_theme['value'] == 'system'
                    if old_light and old_light['value'] and old_theme is None:
                        use_ubuntu = False
                    self.db.execute(
                        "INSERT INTO preferences (key, value) VALUES ('ubuntu_theme', ?)",
                        ('1' if use_ubuntu else '0',),
                    )
                if version < 2:
                    self.db.execute(
                        "ALTER TABLE tasks ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"
                    )
                    self.db.execute("UPDATE tasks SET sort_order = -id")
                    self.db.execute("PRAGMA user_version = 2")
                self.db.execute(
                    "CREATE INDEX IF NOT EXISTS tasks_sort_order_idx ON tasks(sort_order, id DESC)"
                )
        except Exception:
            self.db.close()
            raise

    def list_tasks(self):
        # Bodies may be large; read them only when a row is expanded.
        return self.db.execute(
            "SELECT id, title, status, created_at FROM tasks ORDER BY sort_order, id DESC"
        ).fetchall()

    def body(self, task_id):
        row = self.db.execute("SELECT body FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise LookupError("Task no longer exists.")
        return row["body"]

    def internal_theme(self):
        row = self.db.execute(
            "SELECT value FROM preferences WHERE key = 'internal_theme'"
        ).fetchone()
        return row["value"] if row is not None else "light"

    def ubuntu_theme_enabled(self):
        row = self.db.execute(
            "SELECT value FROM preferences WHERE key = 'ubuntu_theme'"
        ).fetchone()
        return row is None or row["value"] == "1"

    def set_appearance(self, mode, use_ubuntu_theme):
        if mode not in ("light", "dark") or type(use_ubuntu_theme) is not bool:
            raise ValueError("Invalid appearance settings.")
        with self.db:
            self.db.executemany(
                "INSERT INTO preferences (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (("internal_theme", mode), ("ubuntu_theme", "1" if use_ubuntu_theme else "0")),
            )

    def add(self, title):
        title = title.strip()
        if not title:
            raise ValueError("A title is required.")
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO tasks (title, created_at, sort_order) "
                "VALUES (?, ?, COALESCE((SELECT MIN(sort_order) - 1 FROM tasks), 0))",
                (title, created_at),
            )
        return dict(id=cursor.lastrowid, title=title, status=0, created_at=created_at)

    def move(self, task_id, position):
        """Move a task to a zero-based position in the saved task list."""
        rows = self.db.execute(
            "SELECT id, sort_order FROM tasks ORDER BY sort_order, id DESC"
        ).fetchall()
        ids = [row["id"] for row in rows]
        if type(position) is not int or not 0 <= position < len(ids):
            raise ValueError("Invalid task position.")
        try:
            old_position = ids.index(task_id)
        except ValueError as error:
            raise LookupError("Task no longer exists.") from error
        if old_position == position:
            return
        ids.pop(old_position)
        ids.insert(position, task_id)
        first, last = sorted((old_position, position))
        with self.db:
            self.db.executemany(
                "UPDATE tasks SET sort_order = ? WHERE id = ?",
                ((rows[index]["sort_order"], ids[index])
                 for index in range(first, last + 1)),
            )

    def update(self, task_id, **fields):
        if not fields or fields.keys() - {"title", "body", "status"}:
            raise ValueError("Invalid task fields.")
        if "title" in fields:
            fields["title"] = fields["title"].strip()
            if not fields["title"]:
                raise ValueError("A title is required.")
        if "status" in fields and (type(fields["status"]) is not int or fields["status"] not in range(3)):
            raise ValueError("Invalid status.")
        # Column names come only from the allowlist above; values are parameters.
        assignments = ", ".join(f"{field} = ?" for field in fields)
        with self.db:
            cursor = self.db.execute(
                f"UPDATE tasks SET {assignments} WHERE id = ?", (*fields.values(), task_id)
            )
            if cursor.rowcount != 1:
                raise LookupError("Task no longer exists.")

    def delete(self, task_id):
        with self.db:
            self.db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    def close(self):
        self.db.close()
