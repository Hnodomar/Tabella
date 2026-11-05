"""Selection-based rich text controls, separate from paragraph layout."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango


class TextStyleToolbar(Gtk.Box):
    """Toggle independent styles on a TextView selection and expose saved ranges."""

    def __init__(self, view, enabled=True, on_style_changed=None):
        super().__init__(spacing=2, visible=enabled)
        self.view = view
        self.buffer = view.get_buffer()
        self.on_style_changed = on_style_changed
        self.enabled = enabled
        self.tags = {
            "bold": self.buffer.create_tag(None, weight=Pango.Weight.BOLD),
            "italic": self.buffer.create_tag(None, style=Pango.Style.ITALIC),
            "underline": self.buffer.create_tag(None, underline=Pango.Underline.SINGLE),
        }
        self.buttons = {}
        self.add_css_class("toolbar")
        self.add_css_class("format-bar")
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Text formatting"])
        for style, markup in (("bold", "<b>B</b>"), ("italic", "<i>I</i>"),
                              ("underline", "<u>U</u>")):
            button = Gtk.ToggleButton(focus_on_click=False)
            button.set_child(Gtk.Label(label=markup, use_markup=True))
            button.set_tooltip_text(style.capitalize())
            button.update_property([Gtk.AccessibleProperty.LABEL], [style.capitalize()])
            button.connect("clicked", self._clicked, style)
            self.buttons[style] = button
            self.append(button)
        self.buffer.connect("mark-set", self.refresh)
        self.buffer.connect("changed", self.refresh)
        self.refresh()

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.set_visible(enabled)
        self.refresh()

    def refresh(self, *_):
        bounds = self.buffer.get_selection_bounds()
        for style, button in self.buttons.items():
            button.set_sensitive(self.enabled and bool(bounds))
            active = False
            if bounds:
                start, end = bounds
                boundary = start.copy()
                boundary.forward_to_tag_toggle(self.tags[style])
                active = start.has_tag(self.tags[style]) and boundary.compare(end) >= 0
            button.set_active(active)

    def _clicked(self, _button, style):
        bounds = self.buffer.get_selection_bounds()
        if not self.enabled or not bounds:
            self.refresh()
            return
        start, end = bounds
        tag = self.tags[style]
        boundary = start.copy()
        boundary.forward_to_tag_toggle(tag)
        if start.has_tag(tag) and boundary.compare(end) >= 0:
            self.buffer.remove_tag(tag, start, end)
        else:
            self.buffer.apply_tag(tag, start, end)
        self.refresh()
        if self.on_style_changed is not None:
            self.on_style_changed()

    def ranges(self):
        """Serialize only explicit styles, using Unicode character offsets."""
        spans = []
        for style, tag in self.tags.items():
            cursor = self.buffer.get_start_iter()
            while not cursor.is_end():
                if cursor.has_tag(tag):
                    start = cursor.get_offset()
                    cursor.forward_to_tag_toggle(tag)
                    spans.append(dict(style=style, start=start, end=cursor.get_offset()))
                elif not cursor.forward_to_tag_toggle(tag):
                    break
        return spans

    def load(self, spans):
        """Restore styles without changing text or notifying the save callback."""
        for tag in self.tags.values():
            self.buffer.remove_tag(tag, self.buffer.get_start_iter(), self.buffer.get_end_iter())
        for span in spans:
            self.buffer.apply_tag(
                self.tags[span["style"]], self.buffer.get_iter_at_offset(span["start"]),
                self.buffer.get_iter_at_offset(span["end"]),
            )
        self.refresh()
