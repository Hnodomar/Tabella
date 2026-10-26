"""Native GTK 4 UI. No polling, web engine, or background service."""

import argparse
from datetime import datetime
from pathlib import Path
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango

from .storage import STATUSES, Store


SYMBOLS = ("○", "◐", "✓")
APP_ID = "io.github.hnodomar.Tabella"
ROW_HEIGHT = 44
ROW_VERTICAL_MARGIN = 4


def scaled_spacing(value, percent):
    return round(value * percent / 100)


APP_CSS = b"""
    headerbar.compact-titlebar {
        min-height: 28px;
        padding: 0;
    }
    headerbar.compact-titlebar button {
        min-height: 0;
        padding: 2px;
        margin: 0;
    }
    headerbar.compact-titlebar menubutton {
        min-height: 0;
        padding: 0;
        margin: 0;
    }
    headerbar.compact-titlebar .group-control > button,
    headerbar.compact-titlebar .group-control > menubutton > button {
        background: transparent;
        border: none;
        box-shadow: none;
    }
    headerbar.compact-titlebar .group-control > button:hover,
    headerbar.compact-titlebar .group-control > menubutton > button:hover {
        background: alpha(currentColor, 0.08);
    }
    button.remove-group-trash {
        color: #c01c28;
    }
    listview.tabella-list > row {
        padding: 0;
        margin: 0;
        border: 0;
    }
    .drag-dot {
        background-color: alpha(currentColor, 0.85);
        border-radius: 4px;
        min-width: 4px;
        min-height: 4px;
    }
    .task-row-header:hover {
        background-color: alpha(currentColor, 0.08);
    }
    .task-row-header button.flat:hover {
        background-color: transparent;
        box-shadow: none;
    }
    .task-row.drop-before {
        box-shadow: inset 0 2px currentColor;
    }
    .task-row.drop-after {
        box-shadow: inset 0 -2px currentColor;
    }
"""


def margins(widget, value):
    for edge in ("top", "bottom", "start", "end"):
        getattr(widget, f"set_margin_{edge}")(value)
    return widget


class Task(GObject.Object):
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, record):
        super().__init__()
        self.id = record["id"]
        self.title = record["title"]
        self.status = record["status"]
        self.group_ids = set(record["group_ids"])
        self.created_at = record["created_at"]
        self.body = None
        self.expanded = False
        self.focus_title = False
        self.notes_height = None


class StatusItem(GObject.Object):
    """The permanent last item in the virtualized list."""


class StatusRow(Gtk.Box):
    def __init__(self, window):
        super().__init__(spacing=8, hexpand=True)
        self.add_css_class("view")
        self.message = Gtk.Label(xalign=0, hexpand=True, wrap=True, selectable=False)
        self.message.add_css_class("dim-label")
        self.message.set_margin_start(8)
        self.message.set_margin_top(4)
        self.message.set_margin_bottom(4)
        self.append(self.message)
        self.retry = Gtk.Button(label="Retry save", visible=False)
        self.retry.set_margin_end(8)
        self.retry.connect("clicked", lambda *_: window.flush())
        self.append(self.retry)


class TaskRow(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("task-row")
        self.window = window
        self.task = None
        self.handler = None
        self.loading = False
        self.details = None

        self.header = Gtk.Box()
        self.header.add_css_class("task-row-header")
        self.append(self.header)
        header = Gtk.Box(spacing=4)
        self.header.append(header)
        self.header_content = header
        self.expand = Gtk.Button(hexpand=True)
        self.expand.add_css_class("flat")
        self.title = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.expand.set_child(self.title)
        self.expand.connect("clicked", self.toggle_expanded)
        header.append(self.expand)
        self.drag_slot = Gtk.Box(width_request=20)
        self.drag_handle = Gtk.Box(visible=window.drag_handle_enabled)
        self.drag_handle.set_tooltip_text("Drag to reorder task")
        self.drag_handle.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Drag to reorder task"]
        )
        self.drag_handle.set_cursor_from_name("grab")
        dots = Gtk.Grid(column_spacing=3, row_spacing=1,
                        halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        for row in range(3):
            for column in range(2):
                dot = Gtk.Box()
                dot.add_css_class("drag-dot")
                dots.attach(dot, column, row, 1, 1)
        self.drag_handle.append(dots)
        self.drag_source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        self.drag_source.set_button(1)
        self.drag_source.connect("prepare", self.prepare_drag)
        self.drag_handle.add_controller(self.drag_source)
        self.drag_slot.append(self.drag_handle)
        header.append(self.drag_slot)
        self.status = Gtk.Button(width_request=54)
        self.status.set_margin_start(4)
        self.status.add_css_class("flat")
        self.status.add_css_class("status")
        self.status.connect("clicked", self.cycle_status)
        header.append(self.status)

        self.drop_target = Gtk.DropTarget.new(Task, Gdk.DragAction.MOVE)
        self.drop_target.connect("motion", self.drag_motion)
        self.drop_target.connect("leave", self.drag_leave)
        self.drop_target.connect("drop", self.drop_task)
        self.add_controller(self.drop_target)

        self.apply_spacing(window.row_spacing_percent)

    def apply_spacing(self, percent):
        row_height = scaled_spacing(ROW_HEIGHT - 2 * ROW_VERTICAL_MARGIN, percent)
        self.set_size_request(-1, row_height)
        self.drag_slot.set_size_request(20, row_height)
        self.drag_handle.set_size_request(20, row_height)
        horizontal_margin = scaled_spacing(8, percent)
        vertical_margin = ROW_VERTICAL_MARGIN * percent // 100
        self.header_content.set_margin_start(horizontal_margin)
        self.header_content.set_margin_end(horizontal_margin)
        self.header_content.set_margin_top(vertical_margin)
        self.header_content.set_margin_bottom(vertical_margin)
        if self.details is not None:
            self.details.set_margin_start(32 + horizontal_margin)
            self.details.set_margin_end(62 + horizontal_margin)
            self.details.set_margin_bottom(4 + vertical_margin)

    def prepare_drag(self, *_):
        if self.task is None:
            return None
        return Gdk.ContentProvider.new_for_value(self.task)

    def drag_motion(self, _, x, y):
        self.remove_css_class("drop-before")
        self.remove_css_class("drop-after")
        self.add_css_class("drop-before" if y < self.get_height() / 2 else "drop-after")
        return Gdk.DragAction.MOVE

    def drag_leave(self, *_):
        self.remove_css_class("drop-before")
        self.remove_css_class("drop-after")

    def drop_task(self, _, task, x, y):
        self.drag_leave()
        if self.task is None:
            return False
        return self.window.move_task(task, self.task, after=y >= self.get_height() / 2)

    def bind(self, task):
        self.task = task
        self.handler = task.connect("changed", self.refresh_header)
        self.refresh_header()
        if task.expanded:
            if self.show_details() and task.focus_title:
                GLib.idle_add(self.focus_new_title, task)

    def focus_new_title(self, task):
        if self.task is task and self.details is not None:
            self.title_entry.grab_focus()
            self.title_entry.select_region(0, -1)
            task.focus_title = False
        return GLib.SOURCE_REMOVE

    def unbind(self):
        self.drag_leave()
        if self.task is not None:
            self.task.disconnect(self.handler)
        self.task = None
        if self.details is not None:
            self.remove(self.details)
            self.details = None

    def refresh_header(self, *_):
        self.title.set_text(self.task.title)
        self.expand.set_tooltip_text(
            f"{'Collapse' if self.task.expanded else 'Expand'}: {self.task.title}"
        )
        self.expand.update_state([Gtk.AccessibleState.EXPANDED], [int(self.task.expanded)])
        self.status.set_label(SYMBOLS[self.task.status])
        description = f"{STATUSES[self.task.status]}; click to mark {STATUSES[(self.task.status + 1) % 3]}"
        self.status.set_tooltip_text(description)
        self.status.update_property([Gtk.AccessibleProperty.LABEL], [description])
        if self.task.status == 2:
            self.title.add_css_class("dim-label")
        else:
            self.title.remove_css_class("dim-label")

    def toggle_expanded(self, *_):
        if self.task.expanded:
            if not self.window.flush():
                return
            self.task.expanded = False
            self.details.set_visible(False)
        else:
            if not self.show_details():
                return
            self.task.expanded = True
        self.refresh_header()

    def show_details(self):
        try:
            if self.task.body is None:
                self.task.body = self.window.store.body(self.task.id)
        except Exception as error:
            self.window.report_error(error)
            return False
        self.loading = True
        if self.details is None:
            self.build_details()
        self.title_entry.set_text(self.task.title)
        self.body_buffer.set_text(self.task.body)
        self.apply_notes_height(self.task.notes_height)
        self.refresh_group_checks()
        self.loading = False
        self.details.set_visible(True)
        return True

    def build_details(self):
        self.details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.append(self.details)
        self.apply_spacing(self.window.row_spacing_percent)
        created = datetime.fromisoformat(self.task.created_at).astimezone()
        date = Gtk.Label(label=created.strftime("Created %d %b %Y at %H:%M:%S %Z"), xalign=0)
        date.add_css_class("dim-label")
        self.details.append(date)

        self.title_entry = Gtk.Entry(hexpand=True)
        self.title_entry.update_property([Gtk.AccessibleProperty.LABEL], ["Task title"])
        self.title_entry.connect("changed", self.title_changed)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", self.title_left)
        self.title_entry.add_controller(focus)
        self.details.append(self.title_entry)
        self.details.append(Gtk.Label(label="Groups", xalign=0))
        self.group_dropdown = Gtk.MenuButton(label="Choose groups")
        self.group_dropdown.update_property([Gtk.AccessibleProperty.LABEL], ["Task groups"])
        self.group_menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.group_checks = {}
        self.refresh_group_checks()
        group_popover = Gtk.Popover()
        group_popover.set_child(margins(self.group_menu, 8))
        self.group_dropdown.set_popover(group_popover)
        self.details.append(self.group_dropdown)
        self.details.append(Gtk.Label(label="Notes", xalign=0))
        text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        text.set_top_margin(8)
        text.set_bottom_margin(8)
        text.set_left_margin(8)
        text.set_right_margin(8)
        text.update_property([Gtk.AccessibleProperty.LABEL], ["Notes"])
        self.notes_view = text
        self.body_buffer = text.get_buffer()
        self.body_buffer.connect("changed", self.body_changed)
        self.notes_scroll = Gtk.ScrolledWindow(min_content_height=110, max_content_height=240)
        self.notes_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.notes_scroll.set_has_frame(True)
        self.notes_scroll.set_child(text)
        notes_overlay = Gtk.Overlay()
        notes_overlay.set_child(self.notes_scroll)
        self.notes_resize_handle = Gtk.Box(
            halign=Gtk.Align.END, valign=Gtk.Align.END,
            visible=self.window.notes_resize_enabled,
        )
        self.notes_resize_handle.append(self.window.icon("resize-grip.svg"))
        self.notes_resize_handle.set_cursor_from_name("ns-resize")
        self.notes_resize_handle.set_tooltip_text("Drag to resize Notes height")
        self.notes_resize_handle.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Resize Notes height"]
        )
        self.notes_resize_drag = Gtk.GestureDrag()
        self.notes_resize_drag.set_button(1)
        self.notes_resize_drag.connect("drag-begin", self.begin_notes_resize)
        self.notes_resize_drag.connect("drag-update", self.update_notes_resize)
        self.notes_resize_handle.add_controller(self.notes_resize_drag)
        notes_overlay.add_overlay(self.notes_resize_handle)
        self.details.append(notes_overlay)
        delete = Gtk.Button(label="Delete task", halign=Gtk.Align.START)
        delete.add_css_class("destructive-action")
        delete.connect("clicked", lambda *_: self.window.confirm_delete(self.task))
        self.details.append(delete)

    def apply_notes_height(self, height):
        minimum = height if height is not None else 110
        maximum = height if height is not None else 240
        if minimum > self.notes_scroll.get_max_content_height():
            self.notes_scroll.set_max_content_height(maximum)
        self.notes_scroll.set_min_content_height(minimum)
        self.notes_scroll.set_max_content_height(maximum)

    def begin_notes_resize(self, gesture, _x, _y):
        self.notes_resize_start_height = max(self.notes_scroll.get_height(), 110)
        _, _, self.notes_resize_start_y = gesture.get_last_event(None).get_position()

    def update_notes_resize(self, gesture, _offset_x, _offset_y):
        if self.task is None or not self.window.notes_resize_enabled:
            return
        _, _, pointer_y = gesture.get_last_event(None).get_position()
        height = max(80, min(600, round(
            self.notes_resize_start_height + pointer_y - self.notes_resize_start_y
        )))
        self.task.notes_height = height
        self.apply_notes_height(height)

    def title_changed(self, entry):
        if self.loading or self.task is None:
            return
        title = entry.get_text().strip()
        if not title:
            entry.add_css_class("error")
            entry.set_tooltip_text("A title is required. The last valid title is kept.")
            return
        entry.remove_css_class("error")
        entry.set_tooltip_text(None)
        if title != self.task.title:
            self.task.title = title
            self.task.emit("changed")
            self.window.queue_edit(self.task, title=title)

    def title_left(self, *_):
        if self.task is not None:
            self.title_entry.set_text(self.task.title)
            self.title_entry.remove_css_class("error")
            self.window.flush()

    def body_changed(self, buffer):
        if self.loading or self.task is None:
            return
        body = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        if body != self.task.body:
            self.task.body = body
            self.window.queue_edit(self.task, body=body)

    def refresh_group_checks(self):
        for group in self.window.groups:
            if group["id"] not in self.group_checks:
                check = Gtk.CheckButton(label=group["name"])
                check.connect("toggled", self.group_changed, group["id"])
                self.group_checks[group["id"]] = check
                self.group_menu.append(check)
        for group_id, check in self.group_checks.items():
            check.set_active(group_id in self.task.group_ids)
        names = [group["name"] for group in self.window.groups
                 if group["id"] in self.task.group_ids]
        self.group_dropdown.set_label(", ".join(names) if names else "Choose groups")

    def group_changed(self, check, group_id):
        if self.loading or self.task is None:
            return
        group_ids = set(self.task.group_ids)
        if check.get_active():
            group_ids.add(group_id)
        else:
            group_ids.discard(group_id)
        if not group_ids:
            self.window.report_error(ValueError("At least one group is required."))
            self.loading = True
            self.refresh_group_checks()
            self.loading = False
            return
        task = self.task
        if group_ids != task.group_ids:
            self.window.change_task_groups(task, group_ids)
        if self.task is task and self.details is not None:
            self.loading = True
            self.refresh_group_checks()
            self.loading = False

    def cycle_status(self, *_):
        status = (self.task.status + 1) % 3
        try:
            self.window.store.update(self.task.id, status=status)
        except Exception as error:
            self.window.report_error(error)
            return
        self.task.status = status
        self.task.emit("changed")
        self.window.update_message(clear_error=True)


class TabellaWindow(Gtk.ApplicationWindow):
    def __init__(self, application, store):
        super().__init__(application=application, title="Tabella", default_width=640, default_height=650)
        self.set_icon_name(APP_ID)
        self.store = store
        self.pending = {}
        self.save_source = 0
        self.save_error = None
        self.export_chooser = None
        self.settings = Gtk.Settings.get_default()
        self.system_theme_name = self.settings.get_property("gtk-theme-name")
        self.system_prefers_dark = self.settings.get_property(
            "gtk-application-prefer-dark-theme"
        )
        self.internal_theme = store.internal_theme()
        self.use_ubuntu_theme = store.ubuntu_theme_enabled()
        self.row_spacing_percent = store.row_spacing_percent()
        self.drag_handle_enabled = store.drag_handle_enabled()
        self.notes_resize_enabled = store.notes_resize_enabled()
        self.status_sort_descending = False
        self.groups = [dict(row) for row in store.list_groups()]
        self.selected_groups = {group["id"] for group in self.groups if group["selected"]}
        self._applying_appearance = False
        self.model = Gio.ListStore.new(GObject.Object)
        self.model.splice(0, 0, [Task(record) for record in store.list_tasks()])
        self.group_filter = Gtk.CustomFilter.new(
            lambda task: bool(task.group_ids & self.selected_groups)
        )
        self.visible_model = Gtk.FilterListModel.new(self.model, self.group_filter)
        self.connect("close-request", self.on_close)
        click = Gtk.GestureClick()
        click.set_button(0)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self.on_window_press)
        self.add_controller(click)
        self.connect("notify::is-active", self.on_window_active_changed)

        header = Gtk.HeaderBar()
        header.add_css_class("compact-titlebar")
        header.set_title_widget(Gtk.Box())
        self.add_button = Gtk.MenuButton()
        self.add_button.add_css_class("flat")
        self.add_button.set_has_frame(False)
        self.add_button.set_child(self.icon("add.svg"))
        self.add_button.set_tooltip_text("Add a task")
        self.add_button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Add a task"]
        )
        self.create_menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.create_title = Gtk.Entry(placeholder_text="Task title", activates_default=True)
        self.create_title.update_property([Gtk.AccessibleProperty.LABEL], ["New task title"])
        self.create_menu.append(self.create_title)
        self.create_menu.append(Gtk.Label(label="Description (optional)", xalign=0))
        self.create_description = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.create_description.update_property(
            [Gtk.AccessibleProperty.LABEL], ["New task description"]
        )
        description_scroll = Gtk.ScrolledWindow(min_content_height=80)
        description_scroll.set_has_frame(True)
        description_scroll.set_child(self.create_description)
        self.create_menu.append(description_scroll)
        self.create_menu.append(Gtk.Label(label="Groups", xalign=0))
        self.create_group_checks = {}
        self.create_groups_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for group in self.groups:
            self.add_create_group_check(group)
        self.create_menu.append(self.create_groups_box)
        self.create_error = Gtk.Label(xalign=0, wrap=True, visible=False)
        self.create_error.add_css_class("error")
        self.create_menu.append(self.create_error)
        self.create_button = Gtk.Button(label="Create", halign=Gtk.Align.END)
        self.create_button.connect("clicked", self.add_task)
        self.create_menu.append(self.create_button)
        self.create_popover = Gtk.Popover()
        self.create_popover.set_child(margins(self.create_menu, 12))
        self.create_popover.connect("show", lambda *_: self.create_title.grab_focus())
        self.add_button.set_popover(self.create_popover)
        header.pack_start(self.add_button)
        group_control = Gtk.Box(spacing=6)
        group_control.add_css_class("group-control")
        self.add_group_button = Gtk.Button()
        self.add_group_button.set_has_frame(False)
        self.add_group_button.set_child(self.icon("add-group.svg"))
        self.add_group_button.set_tooltip_text("Add a group")
        self.add_group_button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Add a group"]
        )
        self.add_group_button.connect("clicked", self.prompt_add_group)
        group_control.append(self.add_group_button)
        self.remove_group_button = Gtk.MenuButton()
        self.remove_group_button.set_has_frame(False)
        self.remove_group_button.set_child(self.icon("remove-group.svg"))
        self.remove_group_button.set_tooltip_text("Remove a group")
        self.remove_group_button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Remove a group"]
        )
        self.remove_groups_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.remove_group_rows = {}
        for group in self.groups:
            self.add_remove_group_row(group)
        remove_popover = Gtk.Popover()
        remove_popover.set_child(margins(self.remove_groups_box, 8))
        self.remove_group_button.set_popover(remove_popover)
        group_control.append(self.remove_group_button)
        self.group_button = Gtk.MenuButton()
        self.group_button.set_has_frame(False)
        self.group_button.set_child(self.icon("filter.svg"))
        self.group_button.set_tooltip_text("Choose visible groups")
        self.group_button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Choose visible groups"]
        )
        self.group_menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.group_checks = {}
        for group in self.groups:
            self.add_group_check(group)
        group_popover = Gtk.Popover()
        group_popover.set_child(margins(self.group_menu, 8))
        self.group_button.set_popover(group_popover)
        group_control.append(self.group_button)
        header.pack_start(group_control)
        self.theme_button = Gtk.Button()
        self.theme_button.add_css_class("flat")
        self.theme_button.set_child(self.icon("sun.svg"))
        self.theme_button.connect("clicked", self.toggle_internal_theme)
        self.sort_button = Gtk.Button()
        self.sort_button.add_css_class("flat")
        self.sort_button.set_child(self.icon("sort-arrows.svg"))
        self.sort_button.connect("clicked", self.toggle_status_sort)
        self.refresh_sort_button()

        self.settings_button = Gtk.MenuButton()
        self.settings_button.set_has_frame(False)
        self.settings_button.set_child(self.icon("settings.svg"))
        self.settings_button.set_tooltip_text("Settings")
        self.settings_button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Settings"]
        )
        popover = Gtk.Popover()
        settings_menu = margins(
            Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8), 12
        )
        settings_heading = Gtk.Label(label="Settings", xalign=0)
        settings_heading.add_css_class("heading")
        settings_menu.append(settings_heading)
        self.ubuntu_theme_check = Gtk.CheckButton(label="Use Ubuntu theme")
        self.ubuntu_theme_check.set_tooltip_text(
            "Follow Ubuntu's appearance; turn off to use the app's light or dark setting"
        )
        self.ubuntu_theme_check.set_active(self.use_ubuntu_theme)
        self._ubuntu_theme_handler = self.ubuntu_theme_check.connect(
            "toggled", self.toggle_ubuntu_theme
        )
        settings_menu.append(self.ubuntu_theme_check)
        self.drag_handle_check = Gtk.CheckButton(label="Show drag handle")
        self.drag_handle_check.set_active(self.drag_handle_enabled)
        self._drag_handle_handler = self.drag_handle_check.connect(
            "toggled", self.toggle_drag_handle
        )
        settings_menu.append(self.drag_handle_check)
        self.notes_resize_check = Gtk.CheckButton(label="Resize Notes height")
        self.notes_resize_check.set_active(self.notes_resize_enabled)
        self._notes_resize_handler = self.notes_resize_check.connect(
            "toggled", self.toggle_notes_resize
        )
        settings_menu.append(self.notes_resize_check)
        settings_menu.append(Gtk.Label(label="Row spacing (%)", xalign=0))
        self.row_spacing_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 40, 100, 1)
        self.row_spacing_scale.set_digits(0)
        self.row_spacing_scale.set_value_pos(Gtk.PositionType.RIGHT)
        self.row_spacing_scale.set_hexpand(True)
        self.row_spacing_scale.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Row spacing percentage"]
        )
        self.row_spacing_scale.set_value(self.row_spacing_percent)
        self.row_spacing_scale.connect("value-changed", self.change_row_spacing)
        settings_menu.append(self.row_spacing_scale)
        self.export_button = Gtk.Button(label="Export", halign=Gtk.Align.START)
        self.export_button.set_tooltip_text("Export all tasks and settings to a backup file")
        self.export_button.connect("clicked", self.export_data)
        settings_menu.append(self.export_button)
        popover.set_child(settings_menu)
        self.settings_button.set_popover(popover)
        header.pack_end(self.settings_button)
        header.pack_end(self.theme_button)
        header.pack_end(self.sort_button)
        self.set_titlebar(header)
        self.settings.connect(
            "notify::gtk-application-prefer-dark-theme", self.on_system_theme_changed
        )
        self.settings.connect(
            "notify::gtk-theme-name", self.on_system_theme_name_changed
        )
        self.apply_appearance()
        self.status_row = StatusRow(self)
        self.message = self.status_row.message
        self.retry = self.status_row.retry
        self.status_model = Gio.ListStore.new(GObject.Object)
        self.status_model.append(StatusItem())
        sections = Gio.ListStore.new(GObject.Object)
        sections.append(self.visible_model)
        sections.append(self.status_model)
        self.list_model = Gtk.FlattenListModel.new(sections)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _, item: item.set_child(TaskRow(self)))
        factory.connect("bind", self.bind_list_item)
        factory.connect("unbind", self.unbind_list_item)
        self.list = Gtk.ListView(model=Gtk.NoSelection.new(self.list_model), factory=factory)
        self.list.add_css_class("tabella-list")
        self.list.set_show_separators(True)
        provider = Gtk.CssProvider()
        provider.load_from_data(APP_CSS)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.scroll = Gtk.ScrolledWindow(vexpand=True, has_frame=False)
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_child(self.list)
        self.set_child(self.scroll)
        self.update_message()

    def bind_list_item(self, _, item):
        item.set_selectable(False)
        item.set_activatable(False)
        if isinstance(item.get_item(), StatusItem):
            if item.get_child() is not self.status_row:
                item.set_child(self.status_row)
        else:
            child = item.get_child()
            if not isinstance(child, TaskRow):
                child = TaskRow(self)
                item.set_child(child)
            child.bind(item.get_item())

    def unbind_list_item(self, _, item):
        child = item.get_child()
        if isinstance(child, TaskRow):
            child.unbind()
        elif child is self.status_row:
            item.set_child(None)

    def change_row_spacing(self, scale):
        percent = round(scale.get_value())
        if percent == self.row_spacing_percent:
            return
        try:
            self.store.set_row_spacing_percent(percent)
        except Exception as error:
            scale.set_value(self.row_spacing_percent)
            self.report_error(error)
            return
        self.row_spacing_percent = percent
        self.apply_row_spacing(self.list)
        self.update_message(clear_error=True)

    def apply_row_spacing(self, widget):
        if isinstance(widget, TaskRow):
            widget.apply_spacing(self.row_spacing_percent)
        child = widget.get_first_child()
        while child is not None:
            self.apply_row_spacing(child)
            child = child.get_next_sibling()

    def toggle_drag_handle(self, button):
        enabled = button.get_active()
        if enabled == self.drag_handle_enabled:
            return
        try:
            self.store.set_drag_handle_enabled(enabled)
        except Exception as error:
            button.handler_block(self._drag_handle_handler)
            button.set_active(self.drag_handle_enabled)
            button.handler_unblock(self._drag_handle_handler)
            self.report_error(error)
            return
        self.drag_handle_enabled = enabled
        self.apply_drag_handle_visibility(self.list)
        self.update_message(clear_error=True)

    def apply_drag_handle_visibility(self, widget):
        if isinstance(widget, TaskRow):
            widget.drag_handle.set_visible(self.drag_handle_enabled)
        child = widget.get_first_child()
        while child is not None:
            self.apply_drag_handle_visibility(child)
            child = child.get_next_sibling()

    def toggle_notes_resize(self, button):
        enabled = button.get_active()
        if enabled == self.notes_resize_enabled:
            return
        try:
            self.store.set_notes_resize_enabled(enabled)
        except Exception as error:
            button.handler_block(self._notes_resize_handler)
            button.set_active(self.notes_resize_enabled)
            button.handler_unblock(self._notes_resize_handler)
            self.report_error(error)
            return
        self.notes_resize_enabled = enabled
        for row in self.task_rows():
            if row.details is not None:
                row.notes_resize_handle.set_visible(enabled)
        self.update_message(clear_error=True)

    def task_rows(self):
        def visit(widget):
            if isinstance(widget, TaskRow):
                yield widget
            child = widget.get_first_child()
            while child is not None:
                yield from visit(child)
                child = child.get_next_sibling()
        return visit(self.list)

    def focused_text_box(self):
        focused = self.get_focus()
        if focused is None:
            return None
        for row in self.task_rows():
            if row.task is None or row.details is None or not row.details.get_visible():
                continue
            for editor, area in ((row.title_entry, row.title_entry),
                                 (row.notes_view, row.notes_scroll)):
                if focused is editor or focused.is_ancestor(editor):
                    return editor, area
        return None

    def on_window_press(self, _, __, x, y):
        text_box = self.focused_text_box()
        if text_box is None:
            return
        editor, area = text_box
        target = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        if target is None or not (target is area or target.is_ancestor(area)):
            GLib.idle_add(self.clear_text_focus, editor)

    def clear_text_focus(self, editor):
        focused = self.get_focus()
        if focused is not None and (focused is editor or focused.is_ancestor(editor)):
            self.set_focus(None)
        return GLib.SOURCE_REMOVE

    def on_window_active_changed(self, *_):
        if not self.get_property("is-active"):
            text_box = self.focused_text_box()
            if text_box is not None:
                self.clear_text_focus(text_box[0])

    def add_group_check(self, group):
        check = Gtk.CheckButton(label=group["name"])
        check.set_active(bool(group["selected"]))
        check.connect("toggled", self.toggle_group, group["id"])
        self.group_checks[group["id"]] = check
        self.group_menu.append(check)

    def add_create_group_check(self, group):
        check = Gtk.CheckButton(label=group["name"])
        self.create_group_checks[group["id"]] = check
        self.create_groups_box.append(check)

    def add_remove_group_row(self, group):
        row = Gtk.Box(spacing=8)
        row.append(Gtk.Label(label=group["name"], xalign=0, hexpand=True))
        button = Gtk.Button()
        button.add_css_class("remove-group-trash")
        button.set_child(Gtk.Image.new_from_icon_name("user-trash-symbolic"))
        button.set_tooltip_text(f"Delete group {group['name']}")
        button.update_property(
            [Gtk.AccessibleProperty.LABEL], [f"Delete group {group['name']}"]
        )
        button.connect("clicked", self.delete_group, group["id"])
        row.append(button)
        self.remove_group_rows[group["id"]] = row
        self.remove_groups_box.append(row)

    def delete_group(self, _button, group_id):
        if not self.flush():
            return
        try:
            self.store.delete_group(group_id)
        except Exception as error:
            self.report_error(error, "Delete group")
            return
        self.groups = [group for group in self.groups if group["id"] != group_id]
        self.selected_groups.discard(group_id)
        self.group_menu.remove(self.group_checks.pop(group_id))
        self.create_groups_box.remove(self.create_group_checks.pop(group_id))
        self.remove_groups_box.remove(self.remove_group_rows.pop(group_id))
        for task_row in self.task_rows():
            if task_row.details is not None:
                check = task_row.group_checks.pop(group_id, None)
                if check is not None:
                    task_row.group_menu.remove(check)
                task_row.refresh_group_checks()
        self.group_filter.changed(Gtk.FilterChange.DIFFERENT)
        self.update_message(clear_error=True)

    def toggle_group(self, check, group_id):
        selected = check.get_active()
        if selected == (group_id in self.selected_groups):
            return
        if not self.flush():
            check.set_active(not selected)
            return
        try:
            self.store.set_group_selected(group_id, selected)
        except Exception as error:
            check.set_active(not selected)
            self.report_error(error)
            return
        if selected:
            self.selected_groups.add(group_id)
        else:
            self.selected_groups.remove(group_id)
        self.group_filter.changed(Gtk.FilterChange.DIFFERENT)
        self.update_message(clear_error=True)

    def prompt_add_group(self, *_):
        dialog = Gtk.Dialog(
            title="Add group", application=self.get_application(),
            transient_for=self, modal=True,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Add", Gtk.ResponseType.ACCEPT)
        entry = Gtk.Entry(placeholder_text="Group name", activates_default=True)
        entry.update_property([Gtk.AccessibleProperty.LABEL], ["Group name"])
        margins(entry, 12)
        dialog.get_content_area().append(entry)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)

        def response(dialog, response_id):
            name = entry.get_text().strip()
            dialog.destroy()
            if response_id != Gtk.ResponseType.ACCEPT:
                return
            try:
                group = self.store.add_group(name)
            except Exception as error:
                self.report_error(error)
                return
            self.groups.append(group)
            self.selected_groups.add(group["id"])
            self.add_group_check(group)
            self.add_create_group_check(group)
            self.add_remove_group_row(group)
            for task_row in self.task_rows():
                if task_row.details is not None:
                    task_row.loading = True
                    task_row.refresh_group_checks()
                    task_row.loading = False
            self.update_message(clear_error=True)

        dialog.connect("response", response)
        dialog.present()
        entry.grab_focus()

    def change_task_groups(self, task, group_ids):
        if not self.flush():
            return False
        try:
            self.store.update(task.id, group_ids=group_ids)
        except Exception as error:
            self.report_error(error)
            return False
        task.group_ids = set(group_ids)
        for task_row in self.task_rows():
            if task_row.task is task and task_row.details is not None:
                task_row.loading = True
                task_row.refresh_group_checks()
                task_row.loading = False
        self.group_filter.changed(Gtk.FilterChange.DIFFERENT)
        self.update_message(clear_error=True)
        return True

    def icon_path(self, name):
        from pathlib import Path
        return Path(__file__).with_name("assets") / name

    def icon(self, name):
        image = Gtk.Image.new_from_file(str(self.icon_path(name)))
        image.set_pixel_size(22)
        return image

    def refresh_theme_button(self):
        effective_mode = (
            "dark" if self.settings.get_property("gtk-application-prefer-dark-theme")
            else "light"
        ) if self.use_ubuntu_theme else self.internal_theme
        target_mode = "light" if effective_mode == "dark" else "dark"
        icon = "sun.svg" if target_mode == "light" else "moon.svg"
        label = f"Switch to {target_mode} mode"
        self.theme_button.set_child(self.icon(icon))
        self.theme_button.set_tooltip_text(label)
        self.theme_button.update_property([Gtk.AccessibleProperty.LABEL], [label])

    def apply_appearance(self):
        if self._applying_appearance:
            return
        if self.use_ubuntu_theme:
            theme_name = self.system_theme_name
            prefers_dark = self.system_prefers_dark
        else:
            # Use GTK's built-in light/dark variants when Ubuntu integration is off.
            theme_name = "Adwaita"
            prefers_dark = self.internal_theme == "dark"
        self._applying_appearance = True
        try:
            self.settings.set_property("gtk-theme-name", theme_name)
            self.settings.set_property("gtk-application-prefer-dark-theme", prefers_dark)
        finally:
            self._applying_appearance = False
        self.refresh_theme_button()

    def on_system_theme_changed(self, *_):
        if self.use_ubuntu_theme:
            self.refresh_theme_button()
        elif not self._applying_appearance:
            self.apply_appearance()

    def on_system_theme_name_changed(self, *_):
        if self.use_ubuntu_theme:
            self.system_theme_name = self.settings.get_property("gtk-theme-name")
            self.refresh_theme_button()
        elif not self._applying_appearance:
            self.apply_appearance()

    def toggle_internal_theme(self, *_):
        effective_mode = (
            "dark" if self.settings.get_property("gtk-application-prefer-dark-theme")
            else "light"
        ) if self.use_ubuntu_theme else self.internal_theme
        mode = "light" if effective_mode == "dark" else "dark"
        try:
            # A direct sun/moon choice takes control from Ubuntu so it visibly
            # switches appearance even when system theme inheritance was on.
            self.store.set_appearance(mode, False)
        except Exception as error:
            self.report_error(error)
            return
        self.internal_theme = mode
        self.use_ubuntu_theme = False
        self.ubuntu_theme_check.handler_block(self._ubuntu_theme_handler)
        self.ubuntu_theme_check.set_active(False)
        self.ubuntu_theme_check.handler_unblock(self._ubuntu_theme_handler)
        self.apply_appearance()

    def toggle_ubuntu_theme(self, button):
        enabled = button.get_active()
        try:
            self.store.set_appearance(self.internal_theme, enabled)
        except Exception as error:
            button.handler_block(self._ubuntu_theme_handler)
            button.set_active(self.use_ubuntu_theme)
            button.handler_unblock(self._ubuntu_theme_handler)
            self.report_error(error)
            return
        self.use_ubuntu_theme = enabled
        self.apply_appearance()

    def add_task(self, *_):
        title = self.create_title.get_text().strip()
        buffer = self.create_description.get_buffer()
        description = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        group_ids = tuple(group_id for group_id, check in self.create_group_checks.items()
                          if check.get_active())
        if not title or not group_ids:
            message = "Enter a task title and choose at least one group."
            self.create_error.set_text(message)
            self.create_error.set_visible(True)
            self.report_error(ValueError(message), "Create task")
            return
        try:
            record = self.store.add(title, group_ids, body=description)
        except Exception as error:
            self.create_error.set_text(str(error))
            self.create_error.set_visible(True)
            self.report_error(error, "Create task")
            return
        self.create_popover.popdown()
        self.create_title.set_text("")
        buffer.set_text("")
        self.create_error.set_visible(False)
        for check in self.create_group_checks.values():
            check.set_active(False)
        task = Task(record)
        self.model.insert(0, task)
        self.update_message(clear_error=True)

    def move_task(self, source, target, *, after):
        if not isinstance(source, Task) or not isinstance(target, Task):
            return False
        source_found, source_index = self.model.find(source)
        target_found, target_index = self.model.find(target)
        if not source_found or not target_found:
            return False
        destination = target_index + int(after)
        if source_index < destination:
            destination -= 1
        if destination == source_index:
            return True
        if not self.flush():
            return False
        try:
            self.store.move(source.id, destination)
        except Exception as error:
            self.report_error(error)
            return False
        self.model.splice(source_index, 1, [])
        self.model.insert(destination, source)
        self.update_message(clear_error=True)
        return True

    def refresh_sort_button(self):
        direction = "completed first" if self.status_sort_descending else "to-do first"
        label = f"Sort tasks by status ({direction})"
        self.sort_button.set_tooltip_text(label)
        self.sort_button.update_property([Gtk.AccessibleProperty.LABEL], [label])

    def toggle_status_sort(self, *_):
        if not self.flush():
            return
        try:
            ids = self.store.sort_by_status(self.status_sort_descending)
        except Exception as error:
            self.report_error(error)
            return
        tasks = {task.id: task for task in self.model}
        self.model.splice(0, self.model.get_n_items(), [tasks[id] for id in ids])
        self.status_sort_descending = not self.status_sort_descending
        self.refresh_sort_button()
        self.update_message(clear_error=True)

    def queue_edit(self, task, **fields):
        self.pending.setdefault(task.id, {}).update(fields)
        if self.save_source:
            GLib.source_remove(self.save_source)
        self.save_source = GLib.timeout_add(250, self.autosave)
        self.update_message()

    def autosave(self):
        self.save_source = 0
        self.flush()
        return GLib.SOURCE_REMOVE

    def flush(self):
        if self.save_source:
            GLib.source_remove(self.save_source)
            self.save_source = 0
        try:
            for task_id, fields in list(self.pending.items()):
                self.store.update(task_id, **fields)
                del self.pending[task_id]
        except Exception as error:
            self.report_error(error)
            return False
        self.save_error = None
        self.update_message()
        return True

    def export_data(self, *_):
        if self.export_chooser is not None:
            return
        self.settings_button.popdown()
        chooser = Gtk.FileChooserNative.new(
            "Export backup", self, Gtk.FileChooserAction.SAVE, "Export", "Cancel"
        )
        chooser.set_modal(True)
        chooser.set_current_name("Tabella-backup.sqlite3")
        chooser.connect("response", self.finish_export)
        self.export_chooser = chooser
        chooser.show()

    def finish_export(self, chooser, response_id):
        file = chooser.get_file() if response_id == Gtk.ResponseType.ACCEPT else None
        chooser.destroy()
        self.export_chooser = None
        if file is None:
            return
        if not self.flush():
            return
        path = file.get_path()
        if path is None:
            self.report_error(ValueError("Choose a local file for the backup."), "Export")
            return
        try:
            self.store.export(path)
        except Exception as error:
            self.report_error(error, "Export")
            return
        self.update_message(clear_error=True)
        self.message.set_text(f"Backup exported: {file.get_basename()}")

    def report_error(self, error, operation="Task operation"):
        self.save_error = str(error)
        self.message.set_text(f"{operation} failed: {error}")
        self.message.add_css_class("error")
        self.retry.set_visible(bool(self.pending))

    def update_message(self, *, clear_error=False):
        count = self.model.get_n_items()
        if clear_error and not self.pending:
            self.save_error = None
        if self.save_error is not None:
            return
        self.message.remove_css_class("error")
        self.retry.set_visible(False)
        state = "Saving…" if self.pending else "All changes saved"
        self.message.set_text(f"{count} {'task' if count == 1 else 'tasks'} · {state}")

    def confirm_delete(self, task):
        dialog = Gtk.MessageDialog(
            application=self.get_application(), transient_for=self, modal=True,
            destroy_with_parent=True, message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE, text="Delete this task?",
        )
        dialog.set_property("secondary-text", task.title)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Delete", Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)

        def response(dialog, response_id):
            dialog.destroy()
            if response_id != Gtk.ResponseType.ACCEPT:
                return
            try:
                self.store.delete(task.id)
            except Exception as error:
                self.report_error(error)
                return
            self.pending.pop(task.id, None)
            found, position = self.model.find(task)
            if found:
                if self.model.get_n_items() == 1:
                    self.add_button.grab_focus()
                self.model.remove(position)
            self.update_message(clear_error=True)

        dialog.connect("response", response)
        dialog.present()

    def on_close(self, *_):
        # Keep the window (and drafts) open if persistence failed.
        return not self.flush()


class TabellaApplication(Gtk.Application):
    def __init__(self, database_path=None):
        # Gio routes subsequent launches to the existing process.
        # An explicit database needs its own process, even when Tabella is already open.
        flags = (Gio.ApplicationFlags.NON_UNIQUE if database_path is not None
                 else Gio.ApplicationFlags.FLAGS_NONE)
        super().__init__(application_id=APP_ID, flags=flags)
        self.database_path = database_path
        self.store = None
        self.exit_status = 0

    def do_startup(self):
        Gtk.Application.do_startup(self)
        # `python -m tabella` otherwise gives X11 windows the class "__main__.py".
        GLib.set_prgname(APP_ID)

    def do_activate(self):
        window = self.get_active_window()
        if window is None:
            try:
                self.store = Store(self.database_path)
                window = TabellaWindow(self, self.store)
            except Exception as error:
                self.exit_status = 1
                print(f"Unable to open Tabella: {error}", file=sys.stderr)
                dialog = Gtk.MessageDialog(
                    application=self, modal=True, message_type=Gtk.MessageType.ERROR,
                    buttons=Gtk.ButtonsType.CLOSE, text="Unable to open Tabella",
                )
                dialog.set_property("secondary-text", str(error))
                dialog.connect("response", lambda *_: self.quit())
                dialog.present()
                return
            provider = Gtk.CssProvider()
            provider.load_from_data(b"""
                .status { font-size: 22px; }
                list row {
                    padding: 0;
                    margin: 0;
                    border: 0;
                }
            """)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        window.present()

    def do_shutdown(self):
        if self.store is not None:
            self.store.close()
        Gtk.Application.do_shutdown(self)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="Tabella", description="A native desktop task list")
    parser.add_argument("--database", metavar="PATH", type=Path,
                        help="use the given SQLite database instead of the default")
    options = parser.parse_args(argv)
    database_path = options.database.expanduser().resolve() if options.database else None
    application = TabellaApplication(database_path=database_path)
    result = application.run([sys.argv[0]])
    return result or application.exit_status
