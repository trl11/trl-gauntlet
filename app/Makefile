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

.PHONY: build check dev help install runtime

help:
	@echo "Gauntlet desktop app"
	@echo ""
	@echo "    make dev               run the shell against the checkout"
	@echo "    make runtime           fetch CPython $(PYTHON_BUILD_VERSION) and install Gauntlet into it"
	@echo "    make build             build the AppImage and the deb"
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
	@$(DESKTOP_RUNTIME)/bin/python -m pip install --no-cache-dir --disable-pip-version-check \
		$(SDK) $(APP)

# electron-builder writes the unpacked tree beside the installers, so the two
# installers are copied out rather than the whole output directory being it.
VERSION = $(shell node -p "require('$(DESKTOP)/package.json').version")

build: install runtime
	@cd $(DESKTOP) && npm run dist
	@mkdir -p $(DIST)
	@cp $(ROOT)/build/app/$(VERSION)/gauntlet-$(VERSION).AppImage $(DIST)/
	@cp $(ROOT)/build/app/$(VERSION)/gauntlet-$(VERSION).deb $(DIST)/
	@echo "dist/gauntlet-$(VERSION).AppImage"
	@echo "dist/gauntlet-$(VERSION).deb"

check: install
	@cd $(DESKTOP) && npm run format-check && npm run lint
