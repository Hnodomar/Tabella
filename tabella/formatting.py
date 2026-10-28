"""Presentation-only formatting for task descriptions."""

import re


class DescriptionFormatter:
    """Keep wrapped list lines aligned with their first word, preserving source text."""

    def __init__(self, view, enabled=True):
        self.view = view
        self.enabled = enabled
        self.tags = []
        view.get_buffer().connect("changed", self.refresh)
        self.refresh()

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.refresh()

    def refresh(self, *_):
        buffer = self.view.get_buffer()
        table = buffer.get_tag_table()
        for tag in self.tags:
            table.remove(tag)
        self.tags.clear()
        if not self.enabled:
            return
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        tags_by_width = {}
        offset = 0
        for line in text.split("\n"):
            match = re.match(r"([ \t]*-[ \t]*)", line)
            if match:
                width, _ = self.view.create_pango_layout(match[1]).get_pixel_size()
                if width not in tags_by_width:
                    # GTK applies a negative indent to continuation rows only.
                    # Keep the paragraph margin unchanged so the dash stays put.
                    tag = buffer.create_tag(
                        None, left_margin=self.view.get_left_margin(),
                        indent=-width,
                    )
                    tags_by_width[width] = tag
                    self.tags.append(tag)
                buffer.apply_tag(
                    tags_by_width[width], buffer.get_iter_at_offset(offset),
                    buffer.get_iter_at_offset(offset + len(line)),
                )
            offset += len(line) + 1
