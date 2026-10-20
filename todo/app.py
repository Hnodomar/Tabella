"""Native GTK 4 UI. No polling, web engine, or background service."""

from datetime import datetime
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango

from .storage import STATUSES, Store


SYMBOLS = ("○", "◐", "✓")
APP_ID = "io.github.simpletodo.Desktop"
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
    .drag-dot {
        background-color: alpha(currentColor, 0.85);
        border-radius: 4px;
        min-width: 4px;
        min-height: 4px;
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
        self.created_at = record["created_at"]
        self.body = None
        self.expanded = False
        self.focus_title = False


class TaskRow(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("task-row")
        self.window = window
        self.task = None
        self.handler = None
        self.loading = False
        self.details = None

        header = Gtk.Box(spacing=4)
        self.append(header)
        self.expand = Gtk.Button(hexpand=True)
        self.expand.add_css_class("flat")
        self.title = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.expand.set_child(self.title)
        self.expand.connect("clicked", self.toggle_expanded)
        header.append(self.expand)
        self.status = Gtk.Button(width_request=54)
        self.status.set_margin_start(4)
        self.status.add_css_class("flat")
        self.status.add_css_class("status")
        self.status.connect("clicked", self.cycle_status)
        header.append(self.status)
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
        self.set_margin_start(scaled_spacing(8, percent))
        self.set_margin_end(scaled_spacing(8, percent))
        self.set_margin_top(ROW_VERTICAL_MARGIN * percent // 100)
        self.set_margin_bottom(ROW_VERTICAL_MARGIN * percent // 100)

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
        if self.details is None:
            self.build_details()
        self.loading = True
        self.title_entry.set_text(self.task.title)
        self.body_buffer.set_text(self.task.body)
        self.loading = False
        self.details.set_visible(True)
        return True

    def build_details(self):
        self.details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.details.set_margin_start(32)
        self.details.set_margin_end(62)
        self.details.set_margin_bottom(4)
        self.append(self.details)
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
        self.details.append(Gtk.Label(label="Notes", xalign=0))
        text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        text.set_top_margin(8)
        text.set_bottom_margin(8)
        text.set_left_margin(8)
        text.set_right_margin(8)
        text.update_property([Gtk.AccessibleProperty.LABEL], ["Notes"])
        self.body_buffer = text.get_buffer()
        self.body_buffer.connect("changed", self.body_changed)
        scroll = Gtk.ScrolledWindow(min_content_height=110, max_content_height=240)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_has_frame(True)
        scroll.set_child(text)
        self.details.append(scroll)
        delete = Gtk.Button(label="Delete task", halign=Gtk.Align.START)
        delete.add_css_class("destructive-action")
        delete.connect("clicked", lambda *_: self.window.confirm_delete(self.task))
        self.details.append(delete)

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


class TodoWindow(Gtk.ApplicationWindow):
    def __init__(self, application, store):
        super().__init__(application=application, title="Simple Todo", default_width=640, default_height=650)
        self.store = store
        self.pending = {}
        self.save_source = 0
        self.save_error = None
        self.settings = Gtk.Settings.get_default()
        self.system_theme_name = self.settings.get_property("gtk-theme-name")
        self.system_prefers_dark = self.settings.get_property(
            "gtk-application-prefer-dark-theme"
        )
        self.internal_theme = store.internal_theme()
        self.use_ubuntu_theme = store.ubuntu_theme_enabled()
        self.row_spacing_percent = store.row_spacing_percent()
        self.drag_handle_enabled = store.drag_handle_enabled()
        self._applying_appearance = False
        self.model = Gio.ListStore.new(GObject.Object)
        self.model.splice(0, 0, [Task(record) for record in store.list_tasks()])
        self.connect("close-request", self.on_close)

        header = Gtk.HeaderBar()
        header.add_css_class("compact-titlebar")
        header.set_title_widget(Gtk.Box())
        self.add_button = Gtk.Button()
        self.add_button.add_css_class("flat")
        self.add_button.set_child(self.icon("add.svg"))
        self.add_button.set_tooltip_text("Add a task")
        self.add_button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Add a task"]
        )
        self.add_button.connect("clicked", self.add_task)
        header.pack_start(self.add_button)
        self.theme_button = Gtk.Button()
        self.theme_button.add_css_class("flat")
        self.theme_button.set_child(self.icon("sun.svg"))
        self.theme_button.connect("clicked", self.toggle_internal_theme)

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
        popover.set_child(settings_menu)
        self.settings_button.set_popover(popover)
        header.pack_end(self.settings_button)
        header.pack_end(self.theme_button)
        self.set_titlebar(header)
        self.settings.connect(
            "notify::gtk-application-prefer-dark-theme", self.on_system_theme_changed
        )
        self.settings.connect(
            "notify::gtk-theme-name", self.on_system_theme_name_changed
        )
        self.apply_appearance()
        root = Gtk.Overlay()
        self.set_child(root)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _, item: item.set_child(TaskRow(self)))
        factory.connect("bind", self.bind_list_item)
        factory.connect("unbind", self.unbind_list_item)
        self.list = Gtk.ListView(model=Gtk.NoSelection.new(self.model), factory=factory)
        self.list.set_show_separators(True)
        self.list.set_margin_bottom(44)
        provider = Gtk.CssProvider()
        provider.load_from_data(APP_CSS)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.scroll = Gtk.ScrolledWindow(vexpand=True, has_frame=False)
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_child(self.list)
        root.set_child(self.scroll)

        footer = Gtk.Box(spacing=8)
        footer.add_css_class("view")
        footer.set_halign(Gtk.Align.FILL)
        footer.set_valign(Gtk.Align.END)
        self.message = Gtk.Label(xalign=0, hexpand=True, wrap=True, selectable=True)
        self.message.add_css_class("dim-label")
        self.message.set_margin_start(8)
        self.message.set_margin_top(4)
        self.message.set_margin_bottom(4)
        footer.append(self.message)
        self.retry = Gtk.Button(label="Retry save", visible=False)
        self.retry.set_margin_end(8)
        self.retry.connect("clicked", lambda *_: self.flush())
        footer.append(self.retry)
        root.add_overlay(footer)
        self.update_message()

    def bind_list_item(self, _, item):
        item.set_selectable(False)
        item.set_activatable(False)
        item.get_child().bind(item.get_item())

    def unbind_list_item(self, _, item):
        child = item.get_child()
        if isinstance(child, TaskRow):
            child.unbind()

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
        try:
            record = self.store.add("New task")
        except Exception as error:
            self.report_error(error)
            return
        task = Task(record)
        task.expanded = True
        task.focus_title = True
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

    def report_error(self, error):
        self.save_error = str(error)
        self.message.set_text(f"Task operation failed: {error}")
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


class TodoApplication(Gtk.Application):
    def __init__(self):
        # Gio routes subsequent launches to the existing process.
        super().__init__(application_id=APP_ID)
        self.store = None
        self.exit_status = 0

    def do_activate(self):
        window = self.get_active_window()
        if window is None:
            try:
                self.store = Store()
                window = TodoWindow(self, self.store)
            except Exception as error:
                self.exit_status = 1
                print(f"Unable to open Simple Todo: {error}", file=sys.stderr)
                dialog = Gtk.MessageDialog(
                    application=self, modal=True, message_type=Gtk.MessageType.ERROR,
                    buttons=Gtk.ButtonsType.CLOSE, text="Unable to open Simple Todo",
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


def main():
    application = TodoApplication()
    result = application.run(sys.argv)
    return result or application.exit_status
