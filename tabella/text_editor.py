"""Shared editor chrome with a fixed formatting header."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject, Gtk


class TextEditorFrame(Gtk.Frame):
    """Keep controls above scrolling content inside a single native border."""

    def __init__(self, toolbar, content):
        super().__init__()
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        layout.append(toolbar)
        self.separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        toolbar.bind_property(
            "visible", self.separator, "visible", GObject.BindingFlags.SYNC_CREATE
        )
        layout.append(self.separator)
        layout.append(content)
        self.set_child(layout)
