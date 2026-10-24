from datetime import datetime
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tabella.storage import Store, database_path, migrate_legacy_database


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
        self.assertEqual(saved, dict(id=task["id"], title="Renamed", status=2,
                                     created_at=created, group_ids=()))
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

    def test_status_sort_toggles_direction_stably_and_persists(self):
        tasks = [self.store.add(str(index)) for index in range(5)]
        for task, status in zip(tasks, (2, 0, 1, 0, 2)):
            self.store.update(task["id"], status=status)
        original = [row["id"] for row in self.store.list_tasks()]
        statuses = {task["id"]: status for task, status in
                    zip(tasks, (2, 0, 1, 0, 2))}
        for descending in (False, True):
            expected = sorted(original, key=lambda task_id: statuses[task_id],
                              reverse=descending)
            self.assertEqual(self.store.sort_by_status(descending), expected)
            self.assertEqual([row["id"] for row in self.store.list_tasks()], expected)
            self.store.close()
            self.store = Store(self.path)
            self.assertEqual([row["id"] for row in self.store.list_tasks()], expected)
        with self.assertRaises(ValueError):
            self.store.sort_by_status("descending")

    def test_status_sort_rolls_back_when_saving_order_fails(self):
        tasks = [self.store.add(str(index)) for index in range(3)]
        self.store.update(tasks[0]["id"], status=2)
        original = [row["id"] for row in self.store.list_tasks()]
        self.store.db.execute(f"""
            CREATE TEMP TRIGGER block_status_sort BEFORE UPDATE OF sort_order ON tasks
            WHEN NEW.id = {tasks[1]['id']}
            BEGIN SELECT RAISE(ABORT, 'blocked'); END
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.sort_by_status()
        self.assertEqual([row["id"] for row in self.store.list_tasks()], original)

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
            self.assertEqual(old_store.db.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertEqual([row["group_ids"] for row in old_store.list_tasks()], [(), ()])
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
        self.store.db.execute("PRAGMA user_version = 5")
        with self.assertRaises(RuntimeError):
            Store(self.path)
        self.assertEqual(self.store.db.execute("PRAGMA user_version").fetchone()[0], 5)

    def test_groups_and_selection_persist_with_task_assignment(self):
        task = self.store.add("Task")
        self.assertEqual([(row["name"], row["selected"]) for row in self.store.list_groups()],
                         [("No group", 1)])
        group = self.store.add_group("  Home  ")
        self.assertEqual(group["name"], "Home")
        self.store.update(task["id"], group_ids=(group["id"],))
        self.store.set_group_selected(0, False)
        self.store.set_group_selected(group["id"], False)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.list_tasks()[0]["group_ids"], (group["id"],))
        self.assertEqual([(row["name"], row["selected"]) for row in self.store.list_groups()],
                         [("No group", 0), ("Home", 0)])
        for invalid in ("", "  ", None):
            with self.assertRaises(ValueError):
                self.store.add_group(invalid)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.add_group("Home")
        with self.assertRaises(ValueError):
            self.store.update(task["id"], group_ids=(999,))
        with self.assertRaises(ValueError):
            self.store.set_group_selected(group["id"], 1)

    def test_task_can_join_multiple_groups_and_return_to_no_group(self):
        task = self.store.add("Shared")
        home = self.store.add_group("Home")
        work = self.store.add_group("Work")
        self.store.update(task["id"], group_ids=(work["id"], home["id"]))
        self.assertEqual(self.store.list_tasks()[0]["group_ids"],
                         (home["id"], work["id"]))
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.list_tasks()[0]["group_ids"],
                         (home["id"], work["id"]))
        self.store.update(task["id"], group_ids=(work["id"],))
        self.assertEqual(self.store.list_tasks()[0]["group_ids"], (work["id"],))
        self.store.update(task["id"], group_ids=())
        self.assertEqual(self.store.list_tasks()[0]["group_ids"], ())
        self.store.delete(task["id"])
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM task_groups"
        ).fetchone()[0], 0)

    def test_invalid_group_membership_preserves_existing_values(self):
        task = self.store.add("Original")
        home = self.store.add_group("Home")
        self.store.update(task["id"], group_ids=(home["id"],))
        for group_ids in ((0,), (999,), (home["id"], home["id"]),
                          (True,), None, "Home"):
            with self.subTest(group_ids=group_ids), self.assertRaises(ValueError):
                self.store.update(task["id"], title="Changed", group_ids=group_ids)
        self.assertEqual(self.store.list_tasks()[0]["title"], "Original")
        self.assertEqual(self.store.list_tasks()[0]["group_ids"], (home["id"],))
        with self.assertRaises(LookupError):
            self.store.update(999, group_ids=(home["id"],))

    def test_version_three_group_assignment_migrates_to_membership(self):
        task = self.store.add("Existing")
        group = self.store.add_group("Work")
        self.store.close()
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TABLE task_groups")
            db.execute("UPDATE tasks SET group_id = ? WHERE id = ?",
                       (group["id"], task["id"]))
            db.execute("PRAGMA user_version = 3")
        self.store = Store(self.path)
        self.assertEqual(self.store.list_tasks()[0]["group_ids"], (group["id"],))
        self.assertEqual(self.store.db.execute("PRAGMA user_version").fetchone()[0], 4)

    def test_export_is_complete_standalone_snapshot(self):
        group = self.store.add_group("Work")
        task = self.store.add("Prepare report")
        self.store.update(task["id"], body="Detailed notes", status=1,
                          group_ids=(group["id"],))
        self.store.set_group_selected(0, False)
        self.store.set_row_spacing_percent(65)
        destination = Path(self.directory.name) / "backup.sqlite3"

        self.store.export(destination)

        with sqlite3.connect(destination) as backup:
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(backup.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertEqual(backup.execute(
                "SELECT title, body, status FROM tasks"
            ).fetchone(), ("Prepare report", "Detailed notes", 1))
            self.assertEqual(backup.execute(
                "SELECT task_id, group_id FROM task_groups"
            ).fetchall(), [(task["id"], group["id"])])
            self.assertEqual(backup.execute(
                "SELECT name, selected FROM groups ORDER BY id"
            ).fetchall(), [("No group", 0), ("Work", 1)])
            self.assertEqual(backup.execute(
                "SELECT value FROM preferences WHERE key = 'row_spacing_percent'"
            ).fetchone()[0], "65")
        self.assertFalse(destination.with_name(destination.name + "-wal").exists())

    def test_export_preserves_existing_file_on_failure_and_rejects_live_database(self):
        destination = Path(self.directory.name) / "backup.sqlite3"
        destination.write_bytes(b"previous backup")
        with self.assertRaises(ValueError):
            self.store.export(self.path)
        with patch("tabella.storage.os.replace", side_effect=OSError("disk is full")):
            with self.assertRaisesRegex(OSError, "disk is full"):
                self.store.export(destination)
        self.assertEqual(destination.read_bytes(), b"previous backup")
        self.assertEqual(list(destination.parent.glob(".backup.sqlite3.*.tmp")), [])
        self.store.export(destination)
        with sqlite3.connect(destination) as backup:
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_data_directory_respects_xdg(self):
        with patch.dict(os.environ, {"XDG_DATA_HOME": self.directory.name}):
            self.assertEqual(database_path(), Path(self.directory.name) / "Tabella/tasks.sqlite3")
        with patch.dict(os.environ, {"XDG_DATA_HOME": "relative/path"}):
            self.assertEqual(database_path(), Path.home() / ".local/share/Tabella/tasks.sqlite3")

    def test_legacy_database_migration_copies_wal_and_preserves_all_data(self):
        root = Path(self.directory.name)
        source = root / "simple-todo/tasks.sqlite3"
        previous = Store(source)
        group = previous.add_group("Home")
        task = previous.add("Remember this")
        previous.update(task["id"], body="Detailed note", status=2,
                        group_ids=(group["id"],))
        previous.set_group_selected(group["id"], False)
        previous.set_row_spacing_percent(60)
        previous.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        previous.update(task["id"], body="Note still in WAL")
        destination = root / "Tabella/tasks.sqlite3"
        try:
            self.assertTrue(migrate_legacy_database(destination))
            with sqlite3.connect(destination) as migrated:
                self.assertEqual(migrated.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                for table in ("tasks", "groups", "task_groups", "settings", "app_preferences", "preferences"):
                    self.assertEqual(migrated.execute(f"SELECT * FROM {table}").fetchall(),
                                     [tuple(row) for row in previous.db.execute(f"SELECT * FROM {table}")])
                self.assertEqual(migrated.execute("PRAGMA user_version").fetchone()[0], 4)
                self.assertEqual(migrated.execute("SELECT body FROM tasks").fetchone()[0],
                                 "Note still in WAL")
            self.assertTrue(source.exists())
            self.assertFalse(migrate_legacy_database(destination))
            with sqlite3.connect(destination) as migrated:
                self.assertEqual(migrated.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
        finally:
            previous.close()

    def test_legacy_migration_does_not_replace_existing_database(self):
        root = Path(self.directory.name)
        source = root / "simple-todo/tasks.sqlite3"
        previous = Store(source)
        previous.add("Old")
        previous.close()
        destination = root / "Tabella/tasks.sqlite3"
        current = Store(destination)
        current.add("Current")
        current.close()
        self.assertFalse(migrate_legacy_database(destination))
        with sqlite3.connect(destination) as migrated:
            self.assertEqual(migrated.execute("SELECT title FROM tasks").fetchall(),
                             [("Current",)])

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

    def test_notes_resize_setting_is_validated_and_survives_restart(self):
        self.assertTrue(self.store.notes_resize_enabled())
        for invalid in (0, 1, "0", None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.store.set_notes_resize_enabled(invalid)
        self.store.set_notes_resize_enabled(False)
        self.assertFalse(self.store.notes_resize_enabled())
        self.store.close()
        self.store = Store(self.path)
        self.assertFalse(self.store.notes_resize_enabled())
        self.store.set_notes_resize_enabled(True)
        self.assertTrue(self.store.notes_resize_enabled())


if __name__ == "__main__":
    unittest.main()
