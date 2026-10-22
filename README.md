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

To add Tabella to your application menu:

```sh
make install
```

Launch **Tabella** from the menu. Run `make install` again after updating the
source.

## Database and backups

The default database is `~/.local/share/Tabella/tasks.sqlite3`. For a separate
task list, run `./Tabella --database /absolute/path/to/work.sqlite`; Tabella
creates the file if needed and opens another instance.

On first launch after upgrading from Simple Todo, Tabella copies the old database
and leaves it in place. Back up tasks, notes, groups, and preferences with
**Settings → Export**.

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
