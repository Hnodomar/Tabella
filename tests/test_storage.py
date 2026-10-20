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
        self.store.db.execute("PRAGMA user_version = 2")
        with self.assertRaises(RuntimeError):
            Store(self.path)
        self.assertEqual(self.store.db.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_data_directory_respects_xdg(self):
        with patch.dict(os.environ, {"XDG_DATA_HOME": self.directory.name}):
            self.assertEqual(database_path(), Path(self.directory.name) / "simple-todo/tasks.sqlite3")
        with patch.dict(os.environ, {"XDG_DATA_HOME": "relative/path"}):
            self.assertEqual(database_path(), Path.home() / ".local/share/simple-todo/tasks.sqlite3")


if __name__ == "__main__":
    unittest.main()
