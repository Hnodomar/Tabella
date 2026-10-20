from datetime import datetime
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from todo.storage import Store, database_path


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "data" / "tasks.sqlite3"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_changes_survive_restart_and_metadata_excludes_body(self):
        task = self.store.add("  Ship it ✓  ")
        created = task["created_at"]
        self.assertIsNotNone(datetime.fromisoformat(created).tzinfo)
        for status in (1, 2, 0):
            self.store.update(task["id"], status=status)
            self.assertEqual(self.store.list_tasks()[0]["status"], status)
        body = "Multiline notes\n日本語\n'quoted'; DROP TABLE tasks; --"
        self.store.update(task["id"], title="Renamed", body=body, status=2)
        self.store.close()
        self.store = Store(self.path)
        saved = dict(self.store.list_tasks()[0])
        self.assertEqual(saved, dict(id=task["id"], title="Renamed", status=2, created_at=created))
        self.assertEqual(self.store.body(task["id"]), body)

    def test_newest_first_and_deletion_persists(self):
        first = self.store.add("First")
        second = self.store.add("Second")
        self.assertEqual([r["id"] for r in self.store.list_tasks()], [second["id"], first["id"]])
        self.store.delete(first["id"])
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(len(self.store.list_tasks()), 1)
        with self.assertRaises(LookupError):
            self.store.body(first["id"])

    def test_reorder_persists_and_new_tasks_start_on_top(self):
        first = self.store.add("First")
        second = self.store.add("Second")
        third = self.store.add("Third")
        self.store.move(first["id"], 0)
        self.assertEqual(
            [row["id"] for row in self.store.list_tasks()],
            [first["id"], third["id"], second["id"]],
        )
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(
            [row["id"] for row in self.store.list_tasks()],
            [first["id"], third["id"], second["id"]],
        )
        fourth = self.store.add("Fourth")
        self.store.move(fourth["id"], 3)
        self.assertEqual(
            [row["id"] for row in self.store.list_tasks()],
            [first["id"], third["id"], second["id"], fourth["id"]],
        )

    def test_reorder_rejects_invalid_moves_and_rolls_back_on_failure(self):
        first = self.store.add("First")
        second = self.store.add("Second")
        third = self.store.add("Third")
        original = [row["id"] for row in self.store.list_tasks()]
        for task_id, position, error in (
            (999, 0, LookupError), (first["id"], -1, ValueError),
            (first["id"], 3, ValueError), (first["id"], True, ValueError),
        ):
            with self.subTest(task_id=task_id, position=position), self.assertRaises(error):
                self.store.move(task_id, position)
        self.store.db.execute(f"""
            CREATE TEMP TRIGGER block_reorder BEFORE UPDATE OF sort_order ON tasks
            WHEN NEW.id = {second['id']}
            BEGIN SELECT RAISE(ABORT, 'blocked'); END
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.move(first["id"], 0)
        self.assertEqual([row["id"] for row in self.store.list_tasks()], original)
        self.store.db.execute("DROP TRIGGER block_reorder")
        self.store.move(first["id"], 0)
        self.assertEqual(
            [row["id"] for row in self.store.list_tasks()],
            [first["id"], third["id"], second["id"]],
        )

    def test_existing_database_migrates_without_changing_task_order(self):
        old_path = Path(self.directory.name) / "old.sqlite3"
        with sqlite3.connect(old_path) as db:
            db.execute("""
                CREATE TABLE tasks (
                    id INTEGER PRIMARY KEY, title TEXT NOT NULL, status INTEGER NOT NULL,
                    created_at TEXT NOT NULL, body TEXT NOT NULL DEFAULT ''
                )
            """)
            db.executemany(
                "INSERT INTO tasks (id, title, status, created_at, body) VALUES (?, ?, 0, ?, ?)",
                ((2, "Older", "2026-01-01T00:00:00+00:00", "Old note"),
                 (7, "Newer", "2026-01-02T00:00:00+00:00", "New note")),
            )
            db.execute("PRAGMA user_version = 1")
        old_store = Store(old_path)
        try:
            self.assertEqual([row["id"] for row in old_store.list_tasks()], [7, 2])
            self.assertEqual(old_store.body(2), "Old note")
            self.assertEqual(old_store.db.execute("PRAGMA user_version").fetchone()[0], 2)
            old_store.move(2, 0)
            self.assertEqual([row["id"] for row in old_store.list_tasks()], [2, 7])
        finally:
            old_store.close()

    def test_empty_notes_and_invalid_edits(self):
        task = self.store.add("Task")
        self.assertEqual(self.store.body(task["id"]), "")
        self.store.update(task["id"], body="Something")
        self.store.update(task["id"], body="")
        self.assertEqual(self.store.body(task["id"]), "")
        for fields in ({"title": " \n "}, {"status": 3}, {"status": -1}, {"status": True},
                       {"created_at": "changed"}, {"id = 1; DROP TABLE tasks": ""}, {}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.store.update(task["id"], **fields)
        with self.assertRaises(ValueError):
            self.store.add(" \n ")
        self.assertEqual(self.store.list_tasks()[0]["title"], "Task")

    def test_failed_transaction_leaves_existing_values(self):
        task = self.store.add("Task")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.update(task["id"], title="Changed", body=None)
        self.assertEqual(self.store.list_tasks()[0]["title"], "Task")
        self.store.update(task["id"], body="Recovered")
        self.assertEqual(self.store.body(task["id"]), "Recovered")

    def test_missing_task_cannot_silently_save(self):
        with self.assertRaises(LookupError):
            self.store.update(999, title="Missing")

    def test_future_database_version_is_rejected(self):
        self.store.db.execute("PRAGMA user_version = 3")
        with self.assertRaises(RuntimeError):
            Store(self.path)
        self.assertEqual(self.store.db.execute("PRAGMA user_version").fetchone()[0], 3)

    def test_data_directory_respects_xdg(self):
        with patch.dict(os.environ, {"XDG_DATA_HOME": self.directory.name}):
            self.assertEqual(database_path(), Path(self.directory.name) / "simple-todo/tasks.sqlite3")
        with patch.dict(os.environ, {"XDG_DATA_HOME": "relative/path"}):
            self.assertEqual(database_path(), Path.home() / ".local/share/simple-todo/tasks.sqlite3")

    def test_row_spacing_is_validated_and_survives_restart(self):
        self.assertEqual(self.store.row_spacing_percent(), 100)
        for invalid in (39, 101, 40.0, True, "80"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.store.set_row_spacing_percent(invalid)
        for percent in (40, 60, 80, 100):
            self.store.set_row_spacing_percent(percent)
            self.assertEqual(self.store.row_spacing_percent(), percent)
        self.store.set_row_spacing_percent(40)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.row_spacing_percent(), 40)

    def test_drag_handle_setting_is_validated_and_survives_restart(self):
        self.assertTrue(self.store.drag_handle_enabled())
        for invalid in (0, 1, "0", None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.store.set_drag_handle_enabled(invalid)
        self.store.set_drag_handle_enabled(False)
        self.assertFalse(self.store.drag_handle_enabled())
        self.store.close()
        self.store = Store(self.path)
        self.assertFalse(self.store.drag_handle_enabled())
        self.store.set_drag_handle_enabled(True)
        self.assertTrue(self.store.drag_handle_enabled())


if __name__ == "__main__":
    unittest.main()
