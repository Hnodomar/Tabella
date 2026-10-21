PREFIX ?= $(HOME)/.local
APP_DIR := $(PREFIX)/share/Tabella
ICON_DIR := $(PREFIX)/share/icons/hicolor/scalable/apps

.PHONY: run test test-ui install

run:
	/usr/bin/python3 -m tabella

test:
	/usr/bin/python3 -m unittest discover -s tests -p 'test_storage.py' -v

test-ui:
	/usr/bin/python3 -m unittest discover -s tests -p 'test_ui.py' -v

install:
	install -d "$(DESTDIR)$(APP_DIR)/tabella/assets" "$(DESTDIR)$(PREFIX)/bin" "$(DESTDIR)$(PREFIX)/share/applications" "$(DESTDIR)$(ICON_DIR)"
	install -m 644 tabella/*.py "$(DESTDIR)$(APP_DIR)/tabella/"
	install -m 644 tabella/assets/*.svg "$(DESTDIR)$(APP_DIR)/tabella/assets/"
	install -m 644 tabella/assets/io.github.hnodomar.Tabella.svg "$(DESTDIR)$(ICON_DIR)/io.github.hnodomar.Tabella.svg"
	install -m 755 Tabella "$(DESTDIR)$(APP_DIR)/Tabella"
	ln -sfn "$(APP_DIR)/Tabella" "$(DESTDIR)$(PREFIX)/bin/Tabella"
	printf '%s\n' '[Desktop Entry]' 'Type=Application' 'Name=Tabella' 'Comment=A small, automatically saved task list' 'Exec="$(APP_DIR)/Tabella"' 'Icon=io.github.hnodomar.Tabella' 'Terminal=false' 'Categories=Office;Utility;' 'StartupNotify=true' 'StartupWMClass=io.github.hnodomar.Tabella' > "$(DESTDIR)$(PREFIX)/share/applications/io.github.hnodomar.Tabella.desktop"
