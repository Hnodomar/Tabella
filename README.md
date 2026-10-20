# Simple Todo

A lightweight native Ubuntu desktop TODO app using GTK 4, system Python, and
SQLite. No pip dependencies, browser runtime, server, or network connection.

## Run

On Ubuntu 22.04 or later, install the runtime packages if needed:

```sh
sudo apt install python3 python3-gi gir1.2-gtk-4.0
```

From this repository:

```sh
./simple-todo
```

Use `/usr/bin/python3 -m todo` or `make run` as alternatives. Use the system
Python; a virtual environment may not include Ubuntu's GTK bindings.

Optional installation into your user's application menu:

```sh
make install
```

Then launch **Simple Todo** from Ubuntu's application menu. Installation copies
the app into `~/.local/share/simple-todo` and adds a command in `~/.local/bin`.
Run `make install` again after updating the source.

## Use

Use the sun/moon button in the title bar to switch between the app's saved
light and dark appearances. Open the cog menu and enable **Use Ubuntu theme**
to inherit Ubuntu's appearance. When system theming is enabled, clicking the
sun/moon button switches to the app's appearance and turns system inheritance
off. Both preferences persist between runs.

- Click the grey **+** row at the top of the list. A new task opens with its
  title selected, ready to replace and edit.
- Click a row's title or disclosure arrow to expand/collapse it.
- Click its status symbol to cycle **○ not done → ◐ in progress → ✓ done → ○**.
  Tooltips and screen-reader labels also describe the status and next action.
- Expanded rows show the local creation date/time, an editable title, optional
  multiline notes, and a delete button with confirmation.
- Tab navigates controls; Enter/Space activates buttons. Empty titles are
  rejected; clearing an existing title retains its last valid value.

Creation, status changes, and deletion commit immediately. Title/notes edits
save automatically after a 250 ms typing pause, with a visible saving indicator.
Collapsing a row or closing the window flushes pending edits immediately. Save
failures remain visible, retain drafts in memory, and prevent closing until a
save succeeds. Force-killing the process can lose edits still in that short
autosave window.

## Storage and performance

Data lives at `$XDG_DATA_HOME/simple-todo/tasks.sqlite3`, normally
`~/.local/share/simple-todo/tasks.sqlite3`, independent of the repository.
Creation timestamps are stored in UTC and displayed in the local timezone.
The appearance preference is stored in the same database. Close the app before
copying the database for backup.

SQLite uses WAL journaling, full commit synchronization, parameterized queries,
and small updates by primary key. Text edits are coalesced to avoid writing on
every keystroke. GTK's [ListView](https://docs.gtk.org/gtk4/class.ListView.html)
recycles row widgets as you scroll; detail editors and note bodies are loaded
only when expanded. Task metadata is loaded once at startup, so initial loading
and model memory are proportional to the number of tasks. There are no idle
polling loops or background workers. Repeated normal launches activate the
existing application through the desktop session bus.

## Checks

```sh
make test       # storage tests; standard library only, no display required
make test-ui    # GTK integration tests; requires a graphical display
```

For a headless machine with `xvfb` installed:

```sh
xvfb-run -a make test-ui
```

Tests use temporary databases and do not modify your task list.
