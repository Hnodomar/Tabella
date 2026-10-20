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

## Checks

```sh
make test       # storage tests; standard library only, no display required
make test-ui    # GTK integration tests; requires the Ubuntu xvfb package
```

The UI tests start and stop their own virtual display, so they do not show
windows on your desktop.

Tests use temporary databases and do not modify your task list.
