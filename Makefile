PREFIX ?= $(HOME)/.local
APP_DIR := $(PREFIX)/share/simple-todo
ICON_DIR := $(PREFIX)/share/icons/hicolor/scalable/apps

.PHONY: run test test-ui install

run:
	/usr/bin/python3 -m todo

test:
	/usr/bin/python3 -m unittest discover -s tests -p 'test_storage.py' -v

test-ui:
	/usr/bin/python3 -m unittest discover -s tests -p 'test_ui.py' -v

install:
	install -d "$(DESTDIR)$(APP_DIR)/todo/assets" "$(DESTDIR)$(PREFIX)/bin" "$(DESTDIR)$(PREFIX)/share/applications" "$(DESTDIR)$(ICON_DIR)"
	install -m 644 todo/*.py "$(DESTDIR)$(APP_DIR)/todo/"
	install -m 644 todo/assets/*.svg "$(DESTDIR)$(APP_DIR)/todo/assets/"
	install -m 644 todo/assets/io.github.simpletodo.Desktop.svg "$(DESTDIR)$(ICON_DIR)/io.github.simpletodo.Desktop.svg"
	install -m 755 simple-todo "$(DESTDIR)$(APP_DIR)/simple-todo"
	ln -sfn "$(APP_DIR)/simple-todo" "$(DESTDIR)$(PREFIX)/bin/simple-todo"
	printf '%s\n' '[Desktop Entry]' 'Type=Application' 'Name=Simple Todo' 'Comment=A small, automatically saved task list' 'Exec="$(APP_DIR)/simple-todo"' 'Icon=io.github.simpletodo.Desktop' 'Terminal=false' 'Categories=Office;Utility;' 'StartupNotify=true' > "$(DESTDIR)$(PREFIX)/share/applications/io.github.simpletodo.Desktop.desktop"
