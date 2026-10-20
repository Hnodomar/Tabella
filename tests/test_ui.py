"""Exercise actual GTK widgets against isolated, on-disk SQLite databases."""

from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

from todo.app import Task, TaskRow, TodoWindow
from todo.storage import Store


def pump(seconds=0.04):
    deadline = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(0.002)


def descendants(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from descendants(child)
        child = child.get_next_sibling()


@unittest.skipUnless(Gtk.init_check(), "A graphical display is required")
class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = Gtk.Application(
            application_id="io.github.simpletodo.Test", flags=Gio.ApplicationFlags.NON_UNIQUE
        )
        cls.application.register(None)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / "tasks.sqlite3")
        self.window = TodoWindow(self.application, self.store)
        self.window.present()
        pump()

    def tearDown(self):
        self.window.flush()
        self.window.destroy()
        pump()
        self.store.close()
        self.directory.cleanup()

    def add_task(self, title="Test task"):
        self.window.new_title.set_text(title)
        self.window.new_title.emit("activate")
        pump()
        task = self.window.model.get_item(0)
        row = next(w for w in descendants(self.window.list)
                   if isinstance(w, TaskRow) and w.task == task)
        return task, row

    def test_add_expand_edit_autosave_and_status_cycle(self):
        self.assertEqual(self.window.stack.get_visible_child_name(), "empty")
        task, row = self.add_task()
        self.assertEqual(self.window.stack.get_visible_child_name(), "tasks")
        self.assertIsNone(task.body)
        self.assertIsNone(row.details)
        row.expand.emit("clicked")
        self.assertTrue(task.expanded)
        row.title_entry.set_text("Updated title")
        row.body_buffer.set_text("A note\nwith two lines")
        self.assertTrue(self.window.pending)
        self.assertIn("Saving", self.window.message.get_text())
        pump(0.32)
        self.assertFalse(self.window.pending)
        self.assertEqual(self.store.list_tasks()[0]["title"], "Updated title")
        self.assertEqual(self.store.body(task.id), "A note\nwith two lines")
        for expected in (1, 2, 0):
            row.status.emit("clicked")
            self.assertEqual(task.status, expected)
            self.assertEqual(self.store.list_tasks()[0]["status"], expected)
            self.assertTrue(task.expanded)
        row.body_buffer.set_text("")
        row.expand.emit("clicked")
        self.assertFalse(task.expanded)
        self.assertEqual(self.store.body(task.id), "")
        self.assertFalse(self.window.pending)

    def test_close_flushes_without_waiting_for_timer(self):
        task, row = self.add_task()
        row.expand.emit("clicked")
        row.body_buffer.set_text("Last keystroke")
        self.assertFalse(self.window.on_close())
        self.assertEqual(self.store.body(task.id), "Last keystroke")
        self.assertEqual(self.window.save_source, 0)

    def test_failed_save_keeps_draft_and_blocks_close_then_retries(self):
        task, row = self.add_task()
        row.expand.emit("clicked")
        with patch.object(self.store, "update", side_effect=sqlite3.OperationalError("disk is full")):
            row.body_buffer.set_text("Do not lose this")
            pump(0.32)
            self.assertIn(task.id, self.window.pending)
            self.assertTrue(self.window.retry.get_visible())
            self.assertTrue(self.window.on_close())
            row.status.emit("clicked")
            self.assertEqual(task.status, 0)
        self.window.retry.emit("clicked")
        self.assertFalse(self.window.pending)
        self.assertEqual(self.store.body(task.id), "Do not lose this")

    def test_row_recycling_does_not_mix_notes_or_lose_drafts(self):
        first, row = self.add_task("First")
        row.expand.emit("clicked")
        row.body_buffer.set_text("First draft")
        second = Task(self.store.add("Second"))
        # Simulate the factory reusing a widget for another task.
        row.unbind()
        row.bind(second)
        row.expand.emit("clicked")
        self.assertEqual(row.body_buffer.get_char_count(), 0)
        row.body_buffer.set_text("Second draft")
        self.window.flush()
        self.assertEqual(self.store.body(first.id), "First draft")
        self.assertEqual(self.store.body(second.id), "Second draft")
        row.unbind()
        row.bind(first)
        self.assertEqual(row.body_buffer.get_text(
            row.body_buffer.get_start_iter(), row.body_buffer.get_end_iter(), True), "First draft")

    def test_blank_title_keeps_last_valid_title(self):
        task, row = self.add_task()
        row.expand.emit("clicked")
        row.title_entry.set_text("")
        row.title_left()
        self.assertEqual(row.title_entry.get_text(), "Test task")
        self.assertEqual(self.store.list_tasks()[0]["title"], "Test task")

    def test_delete_requires_confirmation(self):
        task, _ = self.add_task()
        for response in (Gtk.ResponseType.CANCEL, Gtk.ResponseType.ACCEPT):
            self.window.confirm_delete(task)
            dialog = next(w for w in self.application.get_windows() if isinstance(w, Gtk.MessageDialog))
            dialog.response(response)
            pump()
            self.assertEqual(self.window.model.get_n_items(),
                             1 if response == Gtk.ResponseType.CANCEL else 0)
        self.assertEqual(self.store.list_tasks(), [])
        self.assertEqual(self.window.stack.get_visible_child_name(), "empty")

    def test_large_list_reuses_widgets_and_keeps_bodies_unloaded(self):
        with self.store.db:
            self.store.db.executemany(
                "INSERT INTO tasks (title, created_at, body) VALUES (?, ?, ?)",
                ((f"Task {index}", "2026-01-01T12:00:00+00:00", "Notes " * 100)
                 for index in range(5000)),
            )
        self.window.model.splice(0, 0, [Task(record) for record in self.store.list_tasks()])
        self.window.update_message()
        pump(0.15)
        rows = [w for w in descendants(self.window.list) if isinstance(w, TaskRow)]
        self.assertLess(len(rows), 500)
        self.assertTrue(all(row.details is None for row in rows))
        self.assertTrue(all(task.body is None for task in self.window.model))
        scroll = self.window.stack.get_child_by_name("tasks")
        adjustment = scroll.get_vadjustment()
        # GTK refines estimated row heights as new rows are measured.
        for _ in range(3):
            adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
            pump(0.08)
        rows = [w for w in descendants(self.window.list) if isinstance(w, TaskRow)]
        self.assertLess(len(rows), 500)
        self.assertTrue(any(row.task.id == 1 for row in rows if row.task is not None))


if __name__ == "__main__":
    unittest.main()
