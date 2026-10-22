"""SQLite persistence; independent of GTK and third-party packages."""

from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import tempfile


STATUSES = ("not done", "in progress", "done")


def database_path():
    data_home = os.environ.get("XDG_DATA_HOME")
    root = Path(data_home) if data_home and Path(data_home).is_absolute() else Path.home() / ".local/share"
    return root / "Tabella" / "tasks.sqlite3"


def migrate_legacy_database(destination):
    """Copy the previous installation's database, including committed WAL data."""
    destination = Path(destination)
    source = destination.parent.parent / "simple-todo" / "tasks.sqlite3"
    if destination.exists() or not source.is_file():
        return False
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=".tasks.sqlite3.", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(source)) as previous, closing(sqlite3.connect(temporary_path)) as copy:
            previous.backup(copy)
            copy.execute("PRAGMA journal_mode = DELETE")
            if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("Migrated database integrity check failed.")
        # Publishing through an exclusive link keeps an existing Tabella database intact.
        try:
            os.link(temporary_path, destination)
        except FileExistsError:
            return False
        return True
    finally:
        Path(temporary_path).unlink(missing_ok=True)


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else database_path()
        if path is None:
            migrate_legacy_database(self.path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=1)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA journal_mode = WAL")
            self.db.execute("PRAGMA synchronous = FULL")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version > 4:
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
                self.db.execute("""
                    CREATE TABLE IF NOT EXISTS groups (
                        id INTEGER PRIMARY KEY,
                        name TEXT NOT NULL UNIQUE CHECK(length(trim(name)) > 0),
                        selected INTEGER NOT NULL DEFAULT 1 CHECK(selected IN (0, 1))
                    )
                """)
                self.db.execute(
                    "INSERT OR IGNORE INTO groups (id, name) VALUES (0, 'No group')"
                )
                if version < 3:
                    self.db.execute(
                        "ALTER TABLE tasks ADD COLUMN group_id INTEGER NOT NULL DEFAULT 0"
                    )
                    self.db.execute("PRAGMA user_version = 3")
                self.db.execute("""
                    CREATE TABLE IF NOT EXISTS task_groups (
                        task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                        group_id INTEGER NOT NULL REFERENCES groups(id),
                        PRIMARY KEY (task_id, group_id)
                    )
                """)
                if version < 4:
                    self.db.execute(
                        "INSERT INTO task_groups (task_id, group_id) "
                        "SELECT id, group_id FROM tasks WHERE group_id != 0"
                    )
                    self.db.execute("PRAGMA user_version = 4")
                self.db.execute(
                    "CREATE INDEX IF NOT EXISTS tasks_sort_order_idx ON tasks(sort_order, id DESC)"
                )
            self.db.execute("PRAGMA foreign_keys = ON")
        except Exception:
            self.db.close()
            raise

    def list_tasks(self):
        # Bodies may be large; read them only when a row is expanded.
        rows = [dict(row) for row in self.db.execute(
            "SELECT id, title, status, created_at FROM tasks ORDER BY sort_order, id DESC"
        )]
        memberships = {}
        for task_id, group_id in self.db.execute(
            "SELECT task_id, group_id FROM task_groups ORDER BY group_id"
        ):
            memberships.setdefault(task_id, []).append(group_id)
        for row in rows:
            row["group_ids"] = tuple(memberships.get(row["id"], ()))
        return rows

    def list_groups(self):
        return self.db.execute(
            "SELECT id, name, selected FROM groups ORDER BY id"
        ).fetchall()

    def add_group(self, name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("A group name is required.")
        name = name.strip()
        with self.db:
            cursor = self.db.execute("INSERT INTO groups (name) VALUES (?)", (name,))
        return dict(id=cursor.lastrowid, name=name, selected=1)

    def set_group_selected(self, group_id, selected):
        if type(selected) is not bool:
            raise ValueError("Group selection must be a boolean.")
        with self.db:
            cursor = self.db.execute(
                "UPDATE groups SET selected = ? WHERE id = ?",
                (int(selected), group_id),
            )
            if cursor.rowcount != 1:
                raise LookupError("Group no longer exists.")

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

    def row_spacing_percent(self):
        row = self.db.execute(
            "SELECT value FROM preferences WHERE key = 'row_spacing_percent'"
        ).fetchone()
        return int(row["value"]) if row is not None else 100

    def set_row_spacing_percent(self, percent):
        if type(percent) is not int or not 40 <= percent <= 100:
            raise ValueError("Row spacing must be between 40% and 100%.")
        with self.db:
            self.db.execute(
                "INSERT INTO preferences (key, value) VALUES ('row_spacing_percent', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(percent),),
            )

    def drag_handle_enabled(self):
        row = self.db.execute(
            "SELECT value FROM preferences WHERE key = 'drag_handle_enabled'"
        ).fetchone()
        return row is None or row["value"] == "1"

    def set_drag_handle_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("Drag handle setting must be a boolean.")
        with self.db:
            self.db.execute(
                "INSERT INTO preferences (key, value) VALUES ('drag_handle_enabled', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("1" if enabled else "0",),
            )

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
        return dict(id=cursor.lastrowid, title=title, status=0,
                    created_at=created_at, group_ids=())

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
        if not fields or fields.keys() - {"title", "body", "status", "group_ids"}:
            raise ValueError("Invalid task fields.")
        if "title" in fields:
            fields["title"] = fields["title"].strip()
            if not fields["title"]:
                raise ValueError("A title is required.")
        if "status" in fields and (type(fields["status"]) is not int or fields["status"] not in range(3)):
            raise ValueError("Invalid status.")
        has_group_ids = "group_ids" in fields
        group_ids = fields.pop("group_ids", None)
        if has_group_ids:
            if not isinstance(group_ids, (list, tuple, set, frozenset)) or any(
                type(group_id) is not int or group_id <= 0 for group_id in group_ids
            ) or len(group_ids) != len(set(group_ids)):
                raise ValueError("Invalid groups.")
            group_ids = tuple(sorted(group_ids))
            if group_ids and self.db.execute(
                f"SELECT COUNT(*) FROM groups WHERE id IN ({','.join('?' for _ in group_ids)})",
                group_ids,
            ).fetchone()[0] != len(group_ids):
                raise ValueError("Invalid groups.")
        # Column names come only from the allowlist above; values are parameters.
        assignments = ", ".join(f"{field} = ?" for field in fields)
        with self.db:
            if self.db.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone() is None:
                raise LookupError("Task no longer exists.")
            if fields:
                self.db.execute(
                    f"UPDATE tasks SET {assignments} WHERE id = ?", (*fields.values(), task_id)
                )
            if has_group_ids:
                self.db.execute("DELETE FROM task_groups WHERE task_id = ?", (task_id,))
                self.db.executemany(
                    "INSERT INTO task_groups (task_id, group_id) VALUES (?, ?)",
                    ((task_id, group_id) for group_id in group_ids),
                )

    def delete(self, task_id):
        with self.db:
            self.db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    def export(self, path):
        """Write a complete, standalone SQLite backup without changing the live database."""
        destination = Path(path)
        if destination.resolve() == self.path.resolve():
            raise ValueError("Choose a different file from the active task database.")
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        os.close(descriptor)
        try:
            with closing(sqlite3.connect(temporary_path)) as backup:
                self.db.backup(backup)
                # Export one self-contained file even when the live database uses WAL.
                backup.execute("PRAGMA journal_mode = DELETE")
                if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise sqlite3.DatabaseError("Backup integrity check failed.")
            os.replace(temporary_path, destination)
        finally:
            Path(temporary_path).unlink(missing_ok=True)

    def close(self):
        self.db.close()
