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

Install the display dependency with `sudo apt install xvfb` if needed. The UI
tests start and stop a private Xvfb display, so their windows do not appear on
your desktop. Run them with `make test-ui`; Xephyr creates a visible window and
should not be used for test runs.

Tests use temporary databases and do not modify your task list.

## Docker development and tests

Build a development image with the app, GTK 4, system Python, Git, Make, and
Xvfb. The Dockerfile pins the Ubuntu base image by digest and uses a dated
Ubuntu package snapshot, so rebuilding it installs the same dependency versions.

```sh
docker build -t simple-todo-dev .
docker run --rm simple-todo-dev                # storage and headless GTK tests
docker run --rm simple-todo-dev make test     # storage tests only
```

To test edits without rebuilding the image, mount the working tree:

```sh
docker run --rm -v "$PWD":/workspace simple-todo-dev
```

The container uses Xvfb for UI tests. To launch the desktop app itself, use
`make run` on Ubuntu as described above.
