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
ADD_TASK_ROW_CSS = b"""
    listview.todo-list > row.activatable {
        background-color: alpha(currentColor, 0.08);
        border: none;
        border-radius: 0;
        box-shadow: none;
        padding: 0;
        margin: 0;
    }
    listview.todo-list > row.activatable:hover {
        background-color: alpha(currentColor, 0.14);
    }
    .add-task-row {
        font-size: 24px;
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


class AddTask(GObject.Object):
    """The permanent first item in the virtualized list."""


class AddTaskRow(Gtk.Box):
    def __init__(self):
        super().__init__(height_request=ROW_HEIGHT, hexpand=True)
        self.add_css_class("add-task-row")
        self.set_tooltip_text("Add a task")
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Add a task"])
        self.append(Gtk.Label(label="+", hexpand=True))


class TaskRow(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.set_size_request(-1, ROW_HEIGHT - 2 * ROW_VERTICAL_MARGIN)
        self.window = window
        self.task = None
        self.handler = None
        self.loading = False
        self.details = None
        margins(self, 8)
        self.set_margin_top(ROW_VERTICAL_MARGIN)
        self.set_margin_bottom(ROW_VERTICAL_MARGIN)

        header = Gtk.Box(spacing=8)
        self.append(header)
        self.expand = Gtk.Button(hexpand=True)
        self.expand.add_css_class("flat")
        caption = Gtk.Box(spacing=10)
        self.arrow = Gtk.Label(label="▸")
        self.title = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        caption.append(self.arrow)
        caption.append(self.title)
        self.expand.set_child(caption)
        self.expand.connect("clicked", self.toggle_expanded)
        header.append(self.expand)
        self.status = Gtk.Button(width_request=54)
        self.status.add_css_class("flat")
        self.status.add_css_class("status")
        self.status.connect("clicked", self.cycle_status)
        header.append(self.status)

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
        if self.task is not None:
            self.task.disconnect(self.handler)
        self.task = None
        if self.details is not None:
            self.remove(self.details)
            self.details = None

    def refresh_header(self, *_):
        self.title.set_text(self.task.title)
        self.arrow.set_text("▾" if self.task.expanded else "▸")
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
        self._applying_appearance = False
        self.model = Gio.ListStore.new(GObject.Object)
        self.model.splice(0, 0, [AddTask(), *[Task(record) for record in store.list_tasks()]])
        self.connect("close-request", self.on_close)

        header = Gtk.HeaderBar()
        header.set_title_widget(Gtk.Label(label="Simple Todo"))
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
        root = margins(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12), 16)
        self.set_child(root)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _, item: item.set_child(TaskRow(self)))
        factory.connect("bind", self.bind_list_item)
        factory.connect("unbind", self.unbind_list_item)
        self.list = Gtk.ListView(model=Gtk.NoSelection.new(self.model), factory=factory)
        self.list.add_css_class("todo-list")
        self.list.set_show_separators(True)
        self.list.set_single_click_activate(True)
        self.list.connect("activate", self.activate_list_item)
        provider = Gtk.CssProvider()
        provider.load_from_data(ADD_TASK_ROW_CSS)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.scroll = Gtk.ScrolledWindow(vexpand=True, has_frame=True)
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_child(self.list)
        root.append(self.scroll)

        footer = Gtk.Box(spacing=8)
        self.message = Gtk.Label(xalign=0, hexpand=True, wrap=True, selectable=True)
        self.message.add_css_class("dim-label")
        footer.append(self.message)
        self.retry = Gtk.Button(label="Retry save", visible=False)
        self.retry.connect("clicked", lambda *_: self.flush())
        footer.append(self.retry)
        root.append(footer)
        self.update_message()

    def bind_list_item(self, _, item):
        item.set_selectable(False)
        model_item = item.get_item()
        item.set_activatable(isinstance(model_item, AddTask))
        child = item.get_child()
        if isinstance(model_item, AddTask):
            if not isinstance(child, AddTaskRow):
                item.set_child(AddTaskRow())
        else:
            if not isinstance(child, TaskRow):
                child = TaskRow(self)
                item.set_child(child)
            child.bind(model_item)

    def activate_list_item(self, _, position):
        if isinstance(self.model.get_item(position), AddTask):
            self.add_task()

    def unbind_list_item(self, _, item):
        child = item.get_child()
        if isinstance(child, TaskRow):
            child.unbind()

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
        self.model.insert(1, task)
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

    def report_error(self, error):
        self.save_error = str(error)
        self.message.set_text(f"Task operation failed: {error}")
        self.message.add_css_class("error")
        self.retry.set_visible(bool(self.pending))

    def update_message(self, *, clear_error=False):
        count = self.model.get_n_items() - 1
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
