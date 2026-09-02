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
# Where a suite belonging to no test programme goes. Nothing is shipped here:
# every suite in this repository belongs to a campaign, so the directory exists
# only once `make suite-new` has scaffolded one into it.
SUITES       := $(ROOT)/suites
CAMPAIGNS    := $(ROOT)/campaigns
# Every suite, wherever it lives. A campaign carries its own, so checking
# $(SUITES) alone silently skips them, and $(SUITES) is only a source once it
# is there to be one.
SUITE_SOURCES := $(wildcard $(SUITES)) $(CAMPAIGNS)
FRONTEND     := $(ROOT)/frontend
# Everything a bench host needs and the desktop bundle cannot carry: the udev
# rules for the devices Gauntlet claims itself, the scripts that install them,
# the services that keep a rig serving, and the landing page it answers port 80
# with. `make rig-build` packages the lot as one deb; the same files also ship
# beside the installers, for a bench that installs by hand.
RIG            := $(ROOT)/rig
UDEV_RULES     := $(RIG)/99-gauntlet-instruments.rules
UDEV_RULES_DIR := /etc/udev/rules.d
HOST_SETUP     := $(RIG)/setup-host.sh
BENCH_SETUP    := $(RIG)/setup-bench.sh
HOST_README    := $(RIG)/README.txt
# What a bench left running as a rig needs on top of that: the backend without
# its window, and the user unit that keeps it serving across reboots.
SERVE_SCRIPT   := $(RIG)/serve-gauntlet.sh
SERVICE_SETUP  := $(RIG)/install-service.sh
SERVICE_UNIT   := $(RIG)/gauntlet.service
# And the landing page a rig answers on port 80 with, so the bare address
# reaches the bench: the page, its banner, the server for it, its unit, and the
# sysctl that lets a user unit bind a port below 1024.
PAGE           := $(RIG)/homepage
PAGE_HTML      := $(PAGE)/homepage.html
PAGE_BANNER    := $(PAGE)/blinky.png
PAGE_SERVE     := $(PAGE)/serve-homepage.py
PAGE_UNIT      := $(PAGE)/gauntlet-homepage.service
PORT_SYSCTL    := $(RIG)/60-gauntlet-unprivileged-ports.conf
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
