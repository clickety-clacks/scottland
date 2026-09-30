# Dev loop: build the plugin into ./build and point this machine's session at the repo.
DEV := $(or $(XDG_DATA_HOME),$(HOME)/.local/share)/scottland/dev
CONF := $(or $(XDG_CONFIG_HOME),$(HOME)/.config)/scottland

.PHONY: plugin dev-install dev-uninstall package clean

plugin:
	meson setup build core/plugin --reconfigure 2>/dev/null || meson setup build core/plugin
	meson compile -C build

dev-install: plugin
	mkdir -p $(DEV)/plugins $(DEV)/metadata $(CONF) $(HOME)/.local/bin
	ln -sf $(CURDIR)/build/libscottland.so $(DEV)/plugins/libscottland.so
	ln -sf $(CURDIR)/core/plugin/metadata/scottland.xml $(DEV)/metadata/scottland.xml
	ln -sf $(CURDIR)/core/config/scottland.ini $(CONF)/scottland.ini
	ln -sf $(CURDIR)/core/session/start-scottland $(HOME)/.local/bin/start-scottland
	mkdir -p $(DEV)/libexec $(DEV)/session-env.d $(DEV)/autostart.d $(DEV)/early-exit.d $(DEV)/config.d $(DEV)/reload.d $(DEV)/accent.d
	ln -sf $(CURDIR)/omarchy/shim/scottland-hyprshim $(DEV)/libexec/scottland-hyprshim
	ln -sf $(CURDIR)/core/session/scottland-build-config $(DEV)/libexec/scottland-build-config
	ln -sf $(CURDIR)/core/session/scottland-autostart $(DEV)/libexec/scottland-autostart
	ln -sf $(CURDIR)/core/session/start-scottland $(DEV)/libexec/start-scottland
	ln -sf $(CURDIR)/core/session/scottland-reload $(DEV)/libexec/scottland-reload
	ln -sf $(CURDIR)/core/session/scottland-reload $(HOME)/.local/bin/scottland-reload
	ln -sf $(CURDIR)/core/libexec/scottland-exec $(HOME)/.local/bin/scottland-exec
	for f in omarchy/libexec/* core/libexec/*; do ln -sf $(CURDIR)/$$f $(DEV)/libexec/$$(basename $$f); done
	ln -sfn $(CURDIR)/core/settings $(DEV)/settings
	mkdir -p $(HOME)/.config/systemd/user
	for f in core/systemd/*; do ln -sf $(CURDIR)/$$f $(HOME)/.config/systemd/user/$$(basename $$f); done
	systemctl --user daemon-reload
	ln -sf $(CURDIR)/core/session/scottland-settings $(HOME)/.local/bin/scottland-settings
	for d in session-env.d autostart.d early-exit.d config.d reload.d accent.d; do \
	  for f in core/$$d/* omarchy/$$d/* omarchy/hooks/*; do \
	    [ -e "$$f" ] || continue; \
	    case $$f in omarchy/hooks/*) [ $$d = early-exit.d ] || continue ;; esac; \
	    ln -sf $(CURDIR)/$$f $(DEV)/$$d/$$(basename $$f); \
	  done; \
	done

dev-uninstall:
	rm -rf $(DEV)
	rm -f $(CONF)/scottland.ini $(HOME)/.local/bin/start-scottland

package:
	cd packaging/arch && makepkg -sif

clean:
	rm -rf build packaging/arch/{src,pkg,*.pkg.tar.*}
