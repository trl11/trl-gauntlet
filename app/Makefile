#
# The desktop shell. `make -C app help`, or the app-* targets at the top level,
# which delegate here.
#

include $(dir $(lastword $(MAKEFILE_LIST)))../common.mk

.DEFAULT_GOAL := help

# A relocatable CPython, not a frozen binary. Suites are separate processes
# that import gauntlet_sdk, and `python` in a manifest resolves to the
# interpreter Gauntlet runs under, so an interpreter has to sit beside it.
# Bump both halves together: the release date is part of the filename.
PYTHON_BUILD_RELEASE ?= 20260728
PYTHON_BUILD_VERSION ?= 3.12.13
PYTHON_BUILD_TARGET  ?= x86_64-unknown-linux-gnu
PYTHON_BUILD_URL = https://github.com/astral-sh/python-build-standalone/releases/download/$(PYTHON_BUILD_RELEASE)/cpython-$(PYTHON_BUILD_VERSION)+$(PYTHON_BUILD_RELEASE)-$(PYTHON_BUILD_TARGET)-install_only.tar.gz

.PHONY: build check dev help host-setup install prune runtime smoke

UNPACKED = $(ROOT)/build/app/$(VERSION)/linux-unpacked

help:
	@echo "Gauntlet desktop app"
	@echo ""
	@echo "    make dev               run the shell against the checkout"
	@echo "    make runtime           fetch CPython $(PYTHON_BUILD_VERSION) and install Gauntlet into it"
	@echo "    make build             build the AppImage and the deb, then smoke them"
	@echo "    make smoke             run what is built with app/runtime moved aside"
	@echo "    make check             format-check, lint and typecheck"

install:
	@test -d $(DESKTOP)/node_modules || \
		(cd $(DESKTOP) && npm install --no-audit --no-fund --loglevel=warn)

# Runs against the checkout: the virtualenv's `gauntlet`, the checkout's
# suites, and output/ for state. The same three things `make run` uses.
dev: install
	@$(MAKE) --no-print-directory -C $(ROOT) ensure-setup frontend
	@cd $(DESKTOP) && npm run build && npm start

# The frontend first, because the wheel carries it and the app has no other
# copy of it.
runtime:
	@$(MAKE) --no-print-directory -C $(ROOT) frontend
	@rm -rf $(DESKTOP_RUNTIME)
	@mkdir -p $(DESKTOP_RUNTIME)
	@echo "CPython $(PYTHON_BUILD_VERSION) ($(PYTHON_BUILD_RELEASE))"
	@curl -fsSL $(PYTHON_BUILD_URL) | tar -xz -C $(DESKTOP_RUNTIME) --strip-components=1
	# -s, because this interpreter shares its minor version with the build
	# host's and would otherwise read ~/.local/lib/pythonX.Y/site-packages.
	# pip counts anything it finds there as already installed and puts nothing
	# in the bundle, so a dependency the build host happens to have is the one
	# the app ships without.
	@$(DESKTOP_RUNTIME)/bin/python -s -m pip install --no-cache-dir --disable-pip-version-check \
		$(SDK) $(APP)
	@$(MAKE) --no-print-directory prune

# The install_only tarball is built to develop against, not to ship: its test
# suite, static library, headers and Tk bindings are most of what the app
# carries and none of what it runs. `make smoke` is what says this went too
# far, by running the packaged app with nothing else to fall back on.
PRUNE_LIB = config-$(basename $(PYTHON_BUILD_VERSION))-* ensurepip idlelib \
            lib2to3 pydoc_data test tkinter turtledemo

prune:
	@cd $(DESKTOP_RUNTIME) && rm -rf include share lib/*.a lib/pkgconfig
	@cd $(DESKTOP_RUNTIME)/lib/python$(basename $(PYTHON_BUILD_VERSION)) && rm -rf $(PRUNE_LIB)
	@find $(DESKTOP_RUNTIME) -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@find $(DESKTOP_RUNTIME) -name "*.pyc" -delete 2>/dev/null || true
	# libpython ships with its debug symbols, which are five sixths of it and
	# the largest thing in the bundle by far. bin/python3.12 is left alone: it
	# strips with "allocated section `.dynstr' not in segment", and what comes
	# out cannot resolve its own symbols.
	@strip --strip-debug $(DESKTOP_RUNTIME)/lib/libpython$(basename $(PYTHON_BUILD_VERSION)).so.1.0
	@echo "runtime $$(du -sh $(DESKTOP_RUNTIME) | cut -f1)"

# electron-builder writes the unpacked tree beside the installers, so the two
# installers are copied out rather than the whole output directory being it. It
# names them for package.json's version, which version-check holds equal to
# VERSION.
build: install runtime
	@cd $(DESKTOP) && npm run dist
	@$(MAKE) --no-print-directory smoke
	@mkdir -p $(DIST)
	@cp $(ROOT)/build/app/$(VERSION)/gauntlet-$(VERSION).AppImage $(DIST)/
	@cp $(ROOT)/build/app/$(VERSION)/gauntlet-$(VERSION).deb $(DIST)/
	@$(MAKE) --no-print-directory host-setup
	@echo "dist/gauntlet-$(VERSION).AppImage"
	@echo "dist/gauntlet-$(VERSION).deb"

# An installer alone is not enough to drive the instruments: the udev rules
# belong to the host, and nothing in a bundle can install them for it. So the
# script, the rules it installs and the note telling someone to run it ship
# beside the installers rather than inside them.
host-setup:
	@mkdir -p $(DIST)
	@install -m 755 $(HOST_SETUP) $(DIST)/
	@install -m 644 $(HOST_README) $(UDEV_RULES) $(DIST)/
	@echo "dist/$(notdir $(HOST_README))"
	@echo "dist/$(notdir $(HOST_SETUP))"
	@echo "dist/$(notdir $(UDEV_RULES))"

# Runs before the installers are copied out, so a bundle that only works on
# the machine that built it never reaches dist/.
smoke:
	@$(DESKTOP)/scripts/smoke.sh $(UNPACKED)

check: install
	@cd $(DESKTOP) && npm run format-check && npm run lint
