#
# The application: discovery, supervisor, API and UI. `make -C packages/gauntlet
# help`, or the gauntlet-* targets at the top level, which delegate here.
#

include $(dir $(lastword $(MAKEFILE_LIST)))../../common.mk

.DEFAULT_GOAL := help

.PHONY: build help

help:
	@echo "gauntlet"
	@echo ""
	@echo "    make build             build the wheel into dist/"

# What a bench host installs to serve Gauntlet without a container or the
# desktop app. The frontend is built first because the wheel carries web_dist
# as package data, and one built without it serves the placeholder instead of
# the UI, which nothing later would report as an error.
build:
	@$(MAKE) --no-print-directory -C $(ROOT) frontend
	@mkdir -p $(DIST)
	@uv build --wheel --out-dir $(DIST) $(APP)
