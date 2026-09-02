#
# Paths and ports, shared by the top-level Makefile and the ones in app/ and
# docker/. Included rather than passed down, so a sub-make invoked directly
# knows as much as one the top level delegated to.
#

SHELL := /bin/bash

# This file sits at the repository root, so its own location is the root no
# matter which directory make was started in.
ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))

# Where a finished artifact lands: the installers, the wheels and the image
# tarball, and nothing any build wrote on the way there.
DIST         := $(ROOT)/dist

# The one number. Every artifact is named for it and every manifest declares
# it; `make version-check` is what keeps those copies honest.
VERSION      := $(shell cat $(ROOT)/VERSION)
VENV         := $(ROOT)/.venv
BIN          := $(VENV)/bin
PY           := $(BIN)/python
APP          := $(ROOT)/packages/gauntlet
SDK          := $(ROOT)/packages/gauntlet-sdk
SUITES       := $(ROOT)/suites
CAMPAIGNS    := $(ROOT)/campaigns
# Every suite, wherever it lives. A campaign carries its own, so checking
# $(SUITES) alone silently skips them.
SUITE_SOURCES := $(SUITES) $(CAMPAIGNS)
FRONTEND     := $(ROOT)/frontend
# What a host needs before it can drive the instruments: the udev rules for the
# devices Gauntlet claims itself, the script that installs them, and the note
# telling whoever unpacks a release to run it. Installed on the bench, not in
# the devcontainer, so all of them ship beside the installers.
SYSTEM         := $(ROOT)/system
UDEV_RULES     := $(SYSTEM)/99-gauntlet-instruments.rules
UDEV_RULES_DIR := /etc/udev/rules.d
HOST_SETUP     := $(SYSTEM)/setup-host.sh
BENCH_SETUP    := $(SYSTEM)/setup-bench.sh
HOST_README    := $(SYSTEM)/README.txt
# And what a bench left running as a rig needs on top of that: the backend
# without its window, and the user unit that keeps it serving across reboots.
SERVE_SCRIPT   := $(SYSTEM)/serve-gauntlet.sh
SERVICE_SETUP  := $(SYSTEM)/install-service.sh
SERVICE_UNIT   := $(SYSTEM)/gauntlet.service
# And the landing page a rig answers on port 80 with, so the bare address
# reaches the bench: the page, the server for it, its unit, and the sysctl that
# lets a user unit bind a port below 1024.
PAGE_HTML      := $(SYSTEM)/homepage.html
PAGE_SERVE     := $(SYSTEM)/serve-homepage.py
PAGE_UNIT      := $(SYSTEM)/homepage.service
PORT_SYSCTL    := $(SYSTEM)/60-gauntlet-unprivileged-ports.conf
FRONTEND_OUT := $(APP)/src/gauntlet/web_dist
# The Electron shell, and the relocatable CPython it ships the backend in.
DESKTOP         := $(ROOT)/app
DESKTOP_RUNTIME := $(DESKTOP)/runtime
DOCKER          := $(ROOT)/docker

# Every port this project binds, in one place so that two of them cannot be
# given the same number.
#
# The port `gauntlet serve` listens on.
APP_PORT ?= 7100
# The port `make frontend-dev` serves the frontend on, declared in
# frontend/vite.config.ts.
FRONTEND_PORT ?= 7101
# The host port `make docker-run` publishes the image on. The socket the
# devcontainer reaches belongs to the host, which already publishes 7100 and
# 7101 for the devcontainer itself, so binding APP_PORT in there fails before
# the container starts. Everywhere else APP_PORT is free and is what
# `make run` would have used.
ifeq ($(GAUNTLET_DEVCONTAINER),true)
DOCKER_PORT ?= 7102
else
DOCKER_PORT ?= $(APP_PORT)
endif

# Every interface, so the app is reachable from another machine and from
# outside a container. Set HOST=127.0.0.1 to keep it on loopback.
HOST ?= 0.0.0.0
