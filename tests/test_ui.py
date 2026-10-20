"""Exercise actual GTK widgets against isolated, on-disk SQLite databases."""

import atexit
from contextlib import ExitStack
import os
from pathlib import Path
import select
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch


_display_resources = ExitStack()
atexit.register(_display_resources.close)
if shutil.which("Xvfb") is None:
    raise RuntimeError("UI tests require Xvfb (install the Ubuntu xvfb package)")
_xvfb = subprocess.Popen(
    ["Xvfb", "-displayfd", "1", "-screen", "0", "1280x1024x24", "-nolisten", "tcp"],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)


def stop_xvfb():
    if _xvfb.poll() is None:
        _xvfb.terminate()
    try:
        _xvfb.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        _xvfb.kill()
        _xvfb.communicate()


_display_resources.callback(stop_xvfb)
if not select.select([_xvfb.stdout], [], [], 5)[0]:
    raise RuntimeError("Timed out waiting for Xvfb to start")
_display_number = _xvfb.stdout.readline().decode().strip()
if not _display_number.isdecimal():
    raise RuntimeError("Xvfb failed to provide a display number")
_display_resources.enter_context(patch.dict(os.environ, {
    "GDK_BACKEND": "x11", "DISPLAY": f":{_display_number}",
}))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from todo.app import Task, TaskRow, TodoWindow
from todo.storage import Store


def tearDownModule():
    _display_resources.close()


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


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        display_ready = Gtk.init_check()
        display = Gdk.Display.get_default()
        if not display_ready or display is None or display.__gtype__.name != "GdkX11Display":
            raise RuntimeError("Could not initialize GTK on the virtual X display")
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
        self.window.add_button.emit("clicked")
        pump()
        task = self.window.model.get_item(0)
        row = next(w for w in descendants(self.window.list)
                   if isinstance(w, TaskRow) and w.task == task)
        row.title_entry.set_text(title)
        self.window.flush()
        row.expand.emit("clicked")
        return task, row

    def test_list_fills_window_and_status_occupies_bottom_edge(self):
        root = self.window.get_child()
        self.assertEqual(root.get_width(), self.window.get_width())
        # The native window border may consume one pixel below the content.
        self.assertIn(
            self.window.get_height()
            - root.get_height()
            - self.window.get_titlebar().get_height(),
            (0, 1),
        )
        self.assertFalse(self.window.scroll.get_has_frame())

        _, list_bounds = self.window.scroll.compute_bounds(root)
        self.assertEqual(list_bounds.get_x(), 0)
        self.assertEqual(list_bounds.get_y(), 0)
        self.assertEqual(list_bounds.get_width(), root.get_width())
        self.assertEqual(list_bounds.get_height(), root.get_height())

        status_bar = self.window.message.get_parent()
        _, status_bounds = status_bar.compute_bounds(root)
        self.assertEqual(status_bounds.get_x(), 0)
        self.assertGreater(status_bounds.get_y(), 0)
        self.assertEqual(status_bounds.get_y() + status_bounds.get_height(), root.get_height())
        self.assertEqual(status_bounds.get_width(), root.get_width())
        self.assertGreaterEqual(self.window.list.get_margin_bottom(), status_bounds.get_height())
        _, message_bounds = self.window.message.compute_bounds(root)
        self.assertLessEqual(message_bounds.get_x(), 8)

    def test_titlebar_is_compact_and_controls_fit(self):
        header = self.window.get_titlebar()
        self.assertTrue(header.has_css_class("compact-titlebar"))
        self.assertIsInstance(header.get_title_widget(), Gtk.Box)
        self.assertIsNone(header.get_title_widget().get_first_child())
        self.assertEqual(self.window.get_title(), "Simple Todo")
        self.assertGreater(header.get_height(), 0)
        self.assertLess(header.get_height(), 36)
        for control in (self.window.add_button, self.window.theme_button,
                        self.window.settings_button):
            self.assertGreater(control.get_height(), 0)
            self.assertLessEqual(control.get_height(), header.get_height())
        _, add_bounds = self.window.add_button.compute_bounds(header)
        _, theme_bounds = self.window.theme_button.compute_bounds(header)
        self.assertLess(add_bounds.get_x(), header.get_width() / 2)
        self.assertGreater(theme_bounds.get_x(), header.get_width() / 2)

    def test_task_title_fills_expand_button_without_arrow(self):
        task, row = self.add_task("A task title that can use the whole row")
        self.assertIs(row.expand.get_child(), row.title)
        self.assertEqual(row.title.get_text(), task.title)
        row.expand.emit("clicked")
        self.assertTrue(task.expanded)
        self.assertIs(row.expand.get_child(), row.title)

    def test_drag_handle_toggle_keeps_title_and_status_in_place(self):
        _, row = self.add_task("A task with a stable title")
        pump()

        def bounds(widget):
            valid, rectangle = widget.compute_bounds(row)
            self.assertTrue(valid)
            return (rectangle.get_x(), rectangle.get_y(),
                    rectangle.get_width(), rectangle.get_height())

        self.assertTrue(self.window.drag_handle_check.get_active())
        self.assertTrue(row.drag_handle.get_visible())
        self.assertIs(row.drag_slot.get_next_sibling(), row.status)
        self.assertLessEqual(row.drag_slot.get_width(), 24)
        original = tuple(bounds(widget) for widget in (row.title, row.status, row.drag_slot))

        self.window.drag_handle_check.set_active(False)
        pump()
        self.assertFalse(row.drag_handle.get_visible())
        self.assertFalse(self.store.drag_handle_enabled())
        self.assertEqual(original, tuple(bounds(widget)
                                         for widget in (row.title, row.status, row.drag_slot)))

        self.window.drag_handle_check.set_active(True)
        pump()
        self.assertTrue(row.drag_handle.get_visible())
        self.assertEqual(original, tuple(bounds(widget)
                                         for widget in (row.title, row.status, row.drag_slot)))

        self.window.drag_handle_check.set_active(False)
        self.window.destroy()
        pump()
        self.window = TodoWindow(self.application, self.store)
        self.window.present()
        pump()
        restored_row = next(w for w in descendants(self.window.list)
                            if isinstance(w, TaskRow) and w.task is not None)
        self.assertFalse(self.window.drag_handle_check.get_active())
        self.assertFalse(restored_row.drag_handle.get_visible())

    def test_drag_handle_toggle_recovers_from_save_failure(self):
        _, row = self.add_task()
        with patch.object(self.store, "set_drag_handle_enabled",
                          side_effect=sqlite3.OperationalError("disk is full")):
            self.window.drag_handle_check.set_active(False)
        self.assertTrue(self.window.drag_handle_check.get_active())
        self.assertTrue(row.drag_handle.get_visible())
        self.assertTrue(self.store.drag_handle_enabled())
        self.assertIn("disk is full", self.window.message.get_text())

    def test_row_spacing_slider_updates_rows_and_persists(self):
        self.assertEqual(self.window.row_spacing_scale.get_value(), 100)
        task, row = self.add_task()
        self.assertEqual(row.get_margin_start(), 8)
        self.assertEqual(row.get_margin_top(), 4)
        self.assertEqual(row.get_size_request()[1], 36)
        self.assertEqual(row.drag_handle.get_size_request()[1], 36)

        self.assertEqual(self.window.row_spacing_scale.get_adjustment().get_lower(), 40)
        self.window.row_spacing_scale.set_value(40)
        pump()
        self.assertEqual(self.store.row_spacing_percent(), 40)
        self.assertEqual(row.get_margin_start(), 3)
        self.assertEqual(row.get_margin_end(), 3)
        self.assertEqual(row.get_margin_top(), 1)
        self.assertEqual(row.get_margin_bottom(), 1)
        self.assertEqual(row.get_size_request()[1], 14)
        self.assertEqual(row.drag_handle.get_size_request()[1], 14)
        self.assertEqual(task.title, "Test task")

        self.window.destroy()
        pump()
        self.window = TodoWindow(self.application, self.store)
        self.window.present()
        pump()
        self.assertEqual(self.window.row_spacing_scale.get_value(), 40)
        restored_row = next(w for w in descendants(self.window.list)
                            if isinstance(w, TaskRow) and w.task is not None)
        self.assertEqual(restored_row.get_margin_start(), 3)
        self.assertEqual(restored_row.drag_handle.get_size_request()[1], 14)
        self.window.row_spacing_scale.set_value(100)
        self.assertEqual(self.store.row_spacing_percent(), 100)
        self.assertEqual(restored_row.get_margin_start(), 8)
        self.assertEqual(restored_row.drag_handle.get_size_request()[1], 36)

    def test_drag_handle_dots_remain_visible_at_compact_spacing(self):
        _, row = self.add_task()
        dots = [widget for widget in descendants(row.drag_handle)
                if widget.has_css_class("drag-dot")]
        self.assertEqual(len(dots), 6)
        self.assertIs(row.drag_source.get_widget(), row.drag_handle)
        for percent in (100, 40):
            self.window.row_spacing_scale.set_value(percent)
            pump()
            self.assertGreaterEqual(row.drag_handle.get_width(), 20)
            for dot in dots:
                self.assertGreaterEqual(dot.get_width(), 4)
                self.assertGreaterEqual(dot.get_height(), 4)
                _, bounds = dot.compute_bounds(row.drag_handle)
                self.assertGreaterEqual(bounds.get_x(), 0)
                self.assertGreaterEqual(bounds.get_y(), 0)
                self.assertLessEqual(bounds.get_x() + bounds.get_width(),
                                     row.drag_handle.get_width())
                self.assertLessEqual(bounds.get_y() + bounds.get_height(),
                                     row.drag_handle.get_height())

    def test_add_expand_edit_autosave_and_status_cycle(self):
        self.assertEqual(self.window.model.get_n_items(), 0)
        task, row = self.add_task()
        self.assertEqual(self.window.model.get_n_items(), 1)
        self.assertEqual(task.body, "")
        self.assertFalse(row.details.get_visible())
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

    def test_titlebar_add_button_creates_and_focuses_an_editable_task(self):
        self.assertEqual(len(self.store.list_tasks()), 0)
        self.assertEqual(self.window.model.get_n_items(), 0)
        self.assertIsInstance(self.window.add_button.get_child(), Gtk.Image)
        self.assertTrue(self.window.icon_path("add.svg").is_file())
        self.assertEqual(self.window.add_button.get_tooltip_text(), "Add a task")
        self.window.add_button.emit("clicked")
        pump()
        task = self.window.model.get_item(0)
        row = next(w for w in descendants(self.window.list)
                   if isinstance(w, TaskRow) and w.task == task)
        self.assertTrue(task.expanded)
        self.assertEqual(row.title_entry.get_text(), "New task")
        focused = self.window.get_focus()
        while focused is not None and focused is not row.title_entry:
            focused = focused.get_parent()
        self.assertIs(focused, row.title_entry)
        row.title_entry.set_text("Groceries")
        self.window.flush()
        self.assertEqual(self.store.list_tasks()[0]["title"], "Groceries")
        row.expand.emit("clicked")
        pump()
        self.assertFalse(row.get_parent().has_css_class("activatable"))
        self.assertEqual(self.window.model.get_n_items(), 1)

    def test_dragging_rows_changes_and_saves_their_order(self):
        first, _ = self.add_task("First")
        second, _ = self.add_task("Second")
        third, _ = self.add_task("Third")

        def row_for(task):
            return next(w for w in descendants(self.window.list)
                        if isinstance(w, TaskRow) and w.task is task)

        source = row_for(first)
        self.assertIsInstance(source.drag_source.emit("prepare", 0, 0), Gdk.ContentProvider)
        self.assertEqual(source.drag_source.get_actions(), Gdk.DragAction.MOVE)
        target = row_for(third)
        self.assertEqual(target.drop_target.emit("motion", 1, 0), Gdk.DragAction.MOVE)
        self.assertTrue(target.has_css_class("drop-before"))
        self.assertTrue(target.drop_target.emit("drop", first, 1, 0))
        pump()
        self.assertEqual([task.id for task in self.window.model],
                         [first.id, third.id, second.id])
        self.assertEqual([row["id"] for row in self.store.list_tasks()],
                         [first.id, third.id, second.id])

        target = row_for(second)
        self.assertTrue(target.drop_target.emit("drop", first, 1, target.get_height()))
        pump()
        self.assertEqual([task.id for task in self.window.model],
                         [third.id, second.id, first.id])
        self.assertFalse(self.window.move_task(object(), second, after=False))
        self.assertIs(self.window.model.get_item(0), third)

        self.window.destroy()
        pump()
        self.window = TodoWindow(self.application, self.store)
        self.window.present()
        pump()
        self.assertEqual([task.id for task in self.window.model],
                         [third.id, second.id, first.id])

    def test_failed_reorder_keeps_the_visible_order_and_saves_drafts(self):
        first, _ = self.add_task("First")
        second, _ = self.add_task("Second")
        first_row = next(w for w in descendants(self.window.list)
                         if isinstance(w, TaskRow) and w.task is first)
        first_row.expand.emit("clicked")
        first_row.body_buffer.set_text("Draft before moving")
        with patch.object(self.store, "move", side_effect=sqlite3.OperationalError("disk is full")):
            self.assertFalse(self.window.move_task(first, second, after=False))
        self.assertEqual([task.id for task in self.window.model],
                         [second.id, first.id])
        self.assertEqual(self.store.body(first.id), "Draft before moving")
        self.assertIn("disk is full", self.window.message.get_text())
        self.assertTrue(self.window.move_task(first, second, after=False))
        self.assertEqual([task.id for task in self.window.model],
                         [first.id, second.id])

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
        self.assertEqual(self.window.model.get_n_items(), 0)

    def test_large_list_reuses_widgets_and_keeps_bodies_unloaded(self):
        with self.store.db:
            self.store.db.executemany(
                "INSERT INTO tasks (title, created_at, body) VALUES (?, ?, ?)",
                ((f"Task {index}", "2026-01-01T12:00:00+00:00", "Notes " * 100)
                 for index in range(5000)),
            )
        self.window.destroy()
        pump()
        self.window = TodoWindow(self.application, self.store)
        self.window.present()
        pump(0.15)
        rows = [w for w in descendants(self.window.list) if isinstance(w, TaskRow)]
        self.assertLess(len(rows), 500)
        self.assertTrue(all(row.details is None for row in rows))
        self.assertTrue(all(task.body is None for task in self.window.model
                            if isinstance(task, Task)))
        if hasattr(self.window.list, "scroll_to"):
            self.window.list.scroll_to(4999, Gtk.ListScrollFlags.NONE, None)
            pump(0.15)
            rows = [w for w in descendants(self.window.list) if isinstance(w, TaskRow)]
            self.assertLess(len(rows), 500)
            self.assertTrue(any(row.task.id == 1 for row in rows if row.task is not None))


if __name__ == "__main__":
    unittest.main()
