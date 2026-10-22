# Tabella

A lightweight native Ubuntu desktop task list using GTK 4, system Python, and
SQLite. No pip dependencies, browser runtime, server, or network connection.

## Run

On Ubuntu 22.04 or later, install the required packages if needed:

```sh
sudo apt install python3 python3-gi gir1.2-gtk-4.0
```

Then run from this repository:

```sh
./Tabella
```

To use a separate task list, pass an absolute database path. Tabella creates the
file if needed and opens it in a separate instance:

```sh
./Tabella --database /absolute/path/to/work.sqlite
```

To add Tabella to your application menu:

```sh
make install
```

Launch **Tabella** from the menu. Run `make install` again after updating the
source.

## Data and backups

By default, Tabella stores your task list in
`~/.local/share/Tabella/tasks.sqlite3`.
On first launch after upgrading from Simple Todo, it copies your previous task
list and leaves the original in place. To save a backup, choose **Settings →
Export**; the export includes tasks, notes, groups, and preferences.

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
docker build -t tabella-dev .
docker run --rm tabella-dev                # storage and headless GTK tests
docker run --rm tabella-dev make test     # storage tests only
```

To test edits without rebuilding the image, mount the working tree:

```sh
docker run --rm -v "$PWD":/workspace tabella-dev
```

The container uses Xvfb for UI tests. To launch the desktop app itself, use
`make run` on Ubuntu as described above.
