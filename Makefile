# Dev loop: build the plugin into ./build and point this machine's session at the repo.
DEV := $(or $(XDG_DATA_HOME),$(HOME)/.local/share)/scottland/dev
CONF := $(or $(XDG_CONFIG_HOME),$(HOME)/.config)/scottland
RELEASES := $(or $(XDG_DATA_HOME),$(HOME)/.local/share)/scottland/releases
# Portal selection for Scottland sessions (xdg-desktop-portal also reads $XDG_DATA_HOME).
PORTALS := $(or $(XDG_DATA_HOME),$(HOME)/.local/share)/xdg-desktop-portal

.PHONY: plugin tools tools-test dev-install link-dev test-hooks hooks dev-uninstall package clean

# Optimized with debug info, asserts and frame pointers: unoptimized builds ran the plugin's CPU
# paths 7-9x slower and froze the pointer (docs/compositor-hangs.md); cores and stacks stay readable.
PLUGIN_OPTS := -Dbuildtype=debugoptimized -Db_ndebug=false -Dcpp_args=-fno-omit-frame-pointer

plugin:
	meson setup build core/plugin --reconfigure $(PLUGIN_OPTS) 2>/dev/null || meson setup build core/plugin $(PLUGIN_OPTS)
	meson compile -C build

# Scottland's system tools (core/tools), installed into build/tools/bin. Every crate under
# core/tools/bin is one command and is built, installed and linked by name: a new tool needs no
# edit here or in the PKGBUILD.
CARGO ?= cargo
tools:
	@for crate in core/tools/bin/*/; do \
	  $(CARGO) install --quiet --locked --path $$crate --root build/tools --target-dir build/cargo --force --no-track || exit 1; \
	done

tools-test:
	$(CARGO) test --locked --manifest-path core/tools/Cargo.toml --target-dir build/cargo

# The user's session runs a snapshot of a commit, never this checkout: merging, testing or editing
# here doesn't touch it until the next dev-install (and reload). Refuses uncommitted work, so what
# runs is exactly a commit. Snapshots are kept (running widgets may still use an older one).
dev-install: plugin tools
	@git diff --quiet HEAD -- . && test -z "$$(git ls-files --others --exclude-standard)" || \
	  { echo "dev-install: commit first; the session runs exactly a commit" >&2; exit 1; }
	@rev=$$(git rev-parse --short=12 HEAD); dest=$(RELEASES)/$$rev; \
	rm -rf "$$dest.new" && mkdir -p "$$dest.new/build" && git archive HEAD | tar -x -C "$$dest.new" && \
	cp build/libscottland.so build/scottland-output-power "$$dest.new/build/" && cp -r build/tools "$$dest.new/build/" && rm -rf "$$dest" && mv "$$dest.new" "$$dest" && \
	$(MAKE) --no-print-directory -C "$$dest" link-dev >/dev/null && echo "installed $$rev ($$dest)"

# Points the user's session at this tree (dev-install runs it inside a snapshot).
link-dev:
	mkdir -p $(DEV)/plugins $(DEV)/metadata $(CONF) $(HOME)/.local/bin
	ln -sf $(CURDIR)/build/libscottland.so $(DEV)/plugins/libscottland.so
	ln -sf $(CURDIR)/core/plugin/metadata/scottland.xml $(DEV)/metadata/scottland.xml
	ln -sf $(CURDIR)/core/config/scottland.ini $(CONF)/scottland.ini
	mkdir -p $(PORTALS) && ln -sf $(CURDIR)/core/config/scottland-portals.conf $(PORTALS)/scottland-portals.conf
	ln -sf $(CURDIR)/core/session/start-scottland $(HOME)/.local/bin/start-scottland
	ln -sf $(CURDIR)/omarchy/bin/scottland-omarchy-setup $(HOME)/.local/bin/scottland-omarchy-setup
	mkdir -p $(DEV)/libexec $(DEV)/session-env.d $(DEV)/autostart.d $(DEV)/early-exit.d $(DEV)/config.d $(DEV)/reload.d $(DEV)/accent.d $(DEV)/focus.d $(DEV)/override-report.d $(DEV)/prompts
	# Keep mode's service handover is gone (2026-10-06); remove only links to the known old files.
	@remove_legacy_link() { \
	  link="$$1"; current="$$2"; suffix="$$3"; \
	  if [ -L "$$link" ]; then \
	    target=$$(readlink -- "$$link") || exit 1; \
	    case "$$target" in \
	      "$$current"|"$(RELEASES)"/[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]/"$$suffix") rm -- "$$link" ;; \
	      *) echo "link-dev: preserving unrecognized handover link" >&2 ;; \
	    esac; \
	  elif [ -e "$$link" ]; then \
	    echo "link-dev: preserving non-link at legacy handover name" >&2; \
	  fi; \
	}; \
	remove_legacy_link "$(DEV)/autostart.d/40-handover" "$(CURDIR)/omarchy/autostart.d/40-handover" "omarchy/autostart.d/40-handover"; \
	remove_legacy_link "$(DEV)/libexec/scottland-handover" "$(CURDIR)/omarchy/libexec/scottland-handover" "omarchy/libexec/scottland-handover"
	ln -sf $(CURDIR)/omarchy/shim/scottland-hyprshim $(DEV)/libexec/scottland-hyprshim
	ln -sf $(CURDIR)/build/scottland-output-power $(DEV)/libexec/scottland-output-power
	ln -sf $(CURDIR)/core/session/scottland-build-config $(DEV)/libexec/scottland-build-config
	ln -sf $(CURDIR)/core/session/scottland-autostart $(DEV)/libexec/scottland-autostart
	ln -sf $(CURDIR)/core/session/start-scottland $(DEV)/libexec/start-scottland
	ln -sf $(CURDIR)/core/session/scottland-reload $(DEV)/libexec/scottland-reload
	ln -sf $(CURDIR)/core/session/scottland-settings $(DEV)/libexec/scottland-settings
	ln -sf $(CURDIR)/core/session/scottland-reload $(HOME)/.local/bin/scottland-reload
	ln -sf $(CURDIR)/core/libexec/scottland-exec $(HOME)/.local/bin/scottland-exec
	ln -sf $(CURDIR)/core/libexec/scottland-ctl $(HOME)/.local/bin/scottland-ctl
	ln -sfn $(CURDIR)/core/agents $(DEV)/agents
	ln -sfn $(CURDIR)/core/widgets $(DEV)/widgets
	for f in omarchy/libexec/* core/libexec/*; do ln -sf $(CURDIR)/$$f $(DEV)/libexec/$$(basename $$f); done
# The system tools, after the scripts: a tool that replaces a script takes its place.
	for f in build/tools/bin/*; do \
	  [ -e "$$f" ] || continue; \
	  ln -sf $(CURDIR)/$$f $(DEV)/libexec/$$(basename $$f); ln -sf $(CURDIR)/$$f $(HOME)/.local/bin/$$(basename $$f); \
	done
	ln -sfn $(CURDIR)/core/settings $(DEV)/settings
	ln -sf $(CURDIR)/omarchy/prompts/omarchy-overrides-agent.txt $(DEV)/prompts/omarchy-overrides-agent.txt
	mkdir -p $(HOME)/.config/systemd/user
	for f in core/systemd/*; do ln -sf $(CURDIR)/$$f $(HOME)/.config/systemd/user/$$(basename $$f); done
	systemctl --user daemon-reload
	ln -sf $(CURDIR)/core/session/scottland-settings $(HOME)/.local/bin/scottland-settings
	for d in session-env.d autostart.d early-exit.d config.d reload.d accent.d focus.d override-report.d; do \
	  for f in core/$$d/* omarchy/$$d/* omarchy/hooks/*; do \
	    [ -e "$$f" ] || continue; \
	    case $$f in omarchy/hooks/*) [ $$d = early-exit.d ] || continue ;; esac; \
	    ln -sf $(CURDIR)/$$f $(DEV)/$$d/$$(basename $$f); \
	  done; \
	done

# Scottland's session helpers (libexec, hook directories, widgets, metadata) for this checkout,
# linked into HOOKS_DIR. dev-install puts them in the dev directory the user's session uses.
hooks:
	mkdir -p $(HOOKS_DIR)/plugins $(HOOKS_DIR)/metadata
	ln -sf $(CURDIR)/build/libscottland.so $(HOOKS_DIR)/plugins/libscottland.so
	ln -sf $(CURDIR)/core/plugin/metadata/scottland.xml $(HOOKS_DIR)/metadata/scottland.xml
	mkdir -p $(HOOKS_DIR)/libexec $(HOOKS_DIR)/session-env.d $(HOOKS_DIR)/autostart.d $(HOOKS_DIR)/early-exit.d $(HOOKS_DIR)/config.d $(HOOKS_DIR)/reload.d $(HOOKS_DIR)/accent.d $(HOOKS_DIR)/focus.d $(HOOKS_DIR)/override-report.d $(HOOKS_DIR)/prompts
	ln -sf $(CURDIR)/omarchy/shim/scottland-hyprshim $(HOOKS_DIR)/libexec/scottland-hyprshim
	ln -sf $(CURDIR)/build/scottland-output-power $(HOOKS_DIR)/libexec/scottland-output-power
	ln -sf $(CURDIR)/core/session/scottland-build-config $(HOOKS_DIR)/libexec/scottland-build-config
	ln -sf $(CURDIR)/core/session/scottland-autostart $(HOOKS_DIR)/libexec/scottland-autostart
	ln -sf $(CURDIR)/core/session/start-scottland $(HOOKS_DIR)/libexec/start-scottland
	ln -sf $(CURDIR)/core/session/scottland-reload $(HOOKS_DIR)/libexec/scottland-reload
	ln -sf $(CURDIR)/core/session/scottland-settings $(HOOKS_DIR)/libexec/scottland-settings
	ln -sfn $(CURDIR)/core/agents $(HOOKS_DIR)/agents
	ln -sfn $(CURDIR)/core/widgets $(HOOKS_DIR)/widgets
	for f in omarchy/libexec/* core/libexec/*; do ln -sf $(CURDIR)/$$f $(HOOKS_DIR)/libexec/$$(basename $$f); done
	for f in build/tools/bin/*; do [ -e "$$f" ] || continue; ln -sf $(CURDIR)/$$f $(HOOKS_DIR)/libexec/$$(basename $$f); done
	ln -sfn $(CURDIR)/core/settings $(HOOKS_DIR)/settings
	ln -sf $(CURDIR)/omarchy/prompts/omarchy-overrides-agent.txt $(HOOKS_DIR)/prompts/omarchy-overrides-agent.txt
	for d in session-env.d autostart.d early-exit.d config.d reload.d accent.d focus.d override-report.d; do \
	  for f in core/$$d/* omarchy/$$d/* omarchy/hooks/*; do \
	    [ -e "$$f" ] || continue; \
	    case $$f in omarchy/hooks/*) [ $$d = early-exit.d ] || continue ;; esac; \
	    ln -sf $(CURDIR)/$$f $(HOOKS_DIR)/$$d/$$(basename $$f); \
	  done; \
	done

# The same, inside this checkout (build/hooks), for its headless test sessions only
# (tests/headless.sh): test sessions of different checkouts on one machine never run each other's
# helpers, and the user's own session is untouched.
test-hooks: plugin tools
	$(MAKE) --no-print-directory hooks HOOKS_DIR=$(CURDIR)/build/hooks

dev-uninstall:
	rm -rf $(DEV)
	rm -f $(CONF)/scottland.ini $(HOME)/.local/bin/start-scottland $(HOME)/.local/bin/scottland-omarchy-setup \
	  $(PORTALS)/scottland-portals.conf

package:
	cd packaging/arch && makepkg -sif

clean:
	rm -rf build packaging/arch/{src,pkg,*.pkg.tar.*}
