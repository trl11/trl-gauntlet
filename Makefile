#
# Gauntlet — test-suite runner. `make help` lists every target.
#

SHELL := /bin/bash
.DEFAULT_GOAL := help

ROOT         := $(abspath $(CURDIR))
VENV         := $(ROOT)/.venv
BIN          := $(VENV)/bin
PY           := $(BIN)/python
APP          := $(ROOT)/packages/gauntlet
DOCKER       := $(ROOT)/docker
SDK          := $(ROOT)/packages/gauntlet-sdk
SUITES       := $(ROOT)/suites
FRONTEND     := $(ROOT)/frontend
FRONTEND_OUT := $(APP)/src/gauntlet/web_dist

# The port `gauntlet serve` listens on.
APP_PORT ?= 7100
# The port `make frontend-dev` serves the frontend on, declared in
# frontend/vite.config.ts.
FRONTEND_PORT ?= 7101
# Every interface, so the app is reachable from another machine and from
# outside a container. Set HOST=127.0.0.1 to keep it on loopback.
HOST ?= 0.0.0.0

include $(ROOT)/.devcontainer/devcontainer.mk


.PHONY: help
help:
	@echo "Gauntlet"
	@echo ""
	@echo "  Setup"
	@echo "    make setup             create .venv and install both packages (editable)"
	@echo "    make clean             remove build artifacts and caches"
	@echo "    make distclean         also remove .venv and output/"
	@echo ""
	@echo "  Devcontainer"
	@echo "    make dev               start the devcontainer and open a shell"
	@echo "    make dev-stop          stop and remove the devcontainer"
	@echo "    make dev-status        show whether the devcontainer is running"
	@echo ""
	@echo "  Develop"
	@echo "    make run               build the frontend and serve (port $(APP_PORT))"
	@echo "    make serve             the same, with auto-reload"
	@echo "    make stop              stop what run or serve started"
	@echo "    make frontend          build the frontend bundle"
	@echo "    make frontend-dev      frontend dev server with hot reload"
	@echo "    make frontend-test     the frontend tests"
	@echo "    make frontend-check    format-check, lint and test the frontend"
	@echo ""
	@echo "  Server"
	@echo "    make docker-build      build the server image"
	@echo "    make docker-run        run it (port $(DOCKER_PORT))"
	@echo "    make docker-stop       stop and remove it"
	@echo ""
	@echo "  Suites"
	@echo "    make suite-new NAME=x  scaffold a suite (TEMPLATE=python|shell)"
	@echo "    make suite-templates   list the available suite templates"
	@echo "    make suite-list        list discovered suites"
	@echo "    make suite-verify      check every suite against the contract"
	@echo "    make suite-verify-run  ...and execute each conformance profile"
	@echo "    make suite-test        each suite's own tests"
	@echo ""
	@echo "  Quality"
	@echo "    make verify            build, check, and run every test"
	@echo "    make check             format-check + lint + typecheck + every test but e2e"
	@echo "    make test              every test: gauntlet, suites, frontend, end to end"
	@echo "    make gauntlet-test     the gauntlet and gauntlet-sdk tests, with coverage"
	@echo "    make test-e2e          drive a real suite run end to end"
	@echo "    make format            auto-format"
	@echo "    make lint              ruff"
	@echo "    make typecheck         mypy"
	@echo ""
	@echo "  Docs"
	@echo "    make schemas           print contract schema names"
	@echo "    make api-spec          write the OpenAPI spec to build/openapi.json"

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

.PHONY: ensure-setup setup

# The devcontainer bind-mounts the repository, so the host and the container
# share .venv but reach it by different absolute paths. Every console script in
# bin/ carries one in its shebang, so a virtualenv built on one side cannot run
# on the other, and reinstalling only this project leaves the tools it did not
# touch broken. Record the path it was built for and start over when it moves.
VENV_ROOT_STAMP := $(VENV)/.gauntlet-root

# uv builds a venv without the python3-venv system package, which the stock
# Debian Python in the devcontainer does not ship.
$(PY):
	@echo "==> creating venv at $(VENV)"
	@uv venv --seed $(VENV)

setup:
	@if [ -d $(VENV) ] && [ "$$(cat $(VENV_ROOT_STAMP) 2>/dev/null)" != "$(ROOT)" ]; then \
		echo "==> .venv was built for another path, recreating it"; \
		rm -rf $(VENV); \
	fi
	@$(MAKE) --no-print-directory $(PY)
	@echo "==> installing gauntlet and gauntlet-sdk (editable)"
	@uv pip install --quiet --python $(PY) -e "$(SDK)[dev]" -e "$(APP)[dev]"
	@echo "$(ROOT)" > $(VENV_ROOT_STAMP)

ensure-setup:
	@{ test -x $(BIN)/gauntlet && \
	   test "$$(cat $(VENV_ROOT_STAMP) 2>/dev/null)" = "$(ROOT)"; } \
		|| $(MAKE) --no-print-directory setup

# ---------------------------------------------------------------------------
# Develop
# ---------------------------------------------------------------------------

.PHONY: frontend frontend-check frontend-dev frontend-install frontend-test run serve stop

run: ensure-setup frontend
	@echo "Gauntlet   http://$(if $(filter 0.0.0.0,$(HOST)),localhost,$(HOST)):$(APP_PORT)"
	@$(BIN)/gauntlet serve --host $(HOST) --port $(APP_PORT)

serve: ensure-setup frontend
	@$(BIN)/gauntlet serve --host $(HOST) --port $(APP_PORT) --reload

# Kills the process group, so a reloader and any suite the run spawned go with
# the server rather than being left behind. Run artifacts are kept; only the
# scratch profiles written for inline-profile runs are removed.
stop:
	@own=$$(ps -o pgid= -p $$$$ | tr -d ' '); \
	stopped=0; \
	for port in $(APP_PORT) $(FRONTEND_PORT); do \
		for pid in $$(lsof -t -iTCP:$$port -sTCP:LISTEN 2>/dev/null); do \
			pgid=$$(ps -o pgid= -p $$pid 2>/dev/null | tr -d ' '); \
			[ -n "$$pgid" ] || continue; \
			if [ "$$pgid" = "$$own" ]; then \
				echo "port $$port: skipping pid $$pid, it shares this shell's process group"; \
				continue; \
			fi; \
			kill -TERM -$$pgid 2>/dev/null || true; \
			for _ in 1 2 3 4 5 6 7 8 9 10; do \
				kill -0 $$pid 2>/dev/null || break; \
				sleep 0.5; \
			done; \
			if kill -0 $$pid 2>/dev/null; then kill -KILL -$$pgid 2>/dev/null || true; fi; \
			echo "stopped pid $$pid on port $$port"; \
			stopped=1; \
		done; \
	done; \
	[ "$$stopped" = "1" ] || echo "nothing was listening on $(APP_PORT) or $(FRONTEND_PORT)"
	@if [ -x $(PY) ]; then \
		runs=$$($(PY) -c "from gauntlet.config import load_settings; print(load_settings().runs_dir)" 2>/dev/null); \
		if [ -n "$$runs" ] && [ -d "$$runs/_scratch" ]; then \
			rm -rf "$$runs/_scratch"; \
			echo "removed $$runs/_scratch"; \
		fi; \
	fi

frontend-install:
	@test -d $(FRONTEND)/node_modules || \
		(cd $(FRONTEND) && npm install --no-audit --no-fund --loglevel=warn)

# Always rebuilds, so `make run` alone is enough after any change.
frontend: frontend-install
	@cd $(FRONTEND) && npm run build

frontend-dev: frontend-install
	@cd $(FRONTEND) && npm run dev

frontend-test: frontend-install
	@cd $(FRONTEND) && npm run test

frontend-check: frontend-install
	@cd $(FRONTEND) && npm run format-check && npm run lint && npm run test

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

.PHONY: docker-build docker-run docker-stop

DOCKER_IMAGE ?= gauntlet:latest
DOCKER_NAME  ?= gauntlet

# The socket the devcontainer reaches belongs to the host, which already
# publishes 7100 and 7101 for the devcontainer itself, so binding APP_PORT in
# there fails before the container starts. Everywhere else APP_PORT is free and
# is what `make run` would have used.
ifeq ($(GAUNTLET_DEVCONTAINER),true)
DOCKER_PORT ?= 7102
else
DOCKER_PORT ?= $(APP_PORT)
endif

docker-build:
	@docker build -f $(DOCKER)/Dockerfile -t $(DOCKER_IMAGE) $(ROOT)

docker-run: docker-build
	@echo "Gauntlet   http://localhost:$(DOCKER_PORT)"
	@docker run --rm --name $(DOCKER_NAME) \
		-p $(DOCKER_PORT):7100 \
		-v gauntlet-data:/data \
		$(DOCKER_IMAGE)

# `docker rm -f` succeeds against a container that is not there, so what was
# removed is decided before removing it rather than from the exit status.
docker-stop:
	@if [ -n "$$(docker ps -aq --filter name=^/$(DOCKER_NAME)$$)" ]; then \
		docker rm -f $(DOCKER_NAME) >/dev/null; \
		echo "stopped $(DOCKER_NAME)"; \
	else \
		echo "$(DOCKER_NAME) is not running"; \
	fi

# ---------------------------------------------------------------------------
# Suites
# ---------------------------------------------------------------------------

.PHONY: suite-list suite-new suite-templates suite-test suite-verify suite-verify-run

suite-new: ensure-setup
	@test -n "$(NAME)" || { echo "usage: make suite-new NAME=my_suite [TEMPLATE=python|shell]"; exit 2; }
	@$(BIN)/gauntlet new-suite $(NAME) --template $(or $(TEMPLATE),python) --into $(SUITES)

suite-templates: ensure-setup
	@$(BIN)/gauntlet templates

suite-list: ensure-setup
	@$(BIN)/gauntlet list --suites $(SUITES)

suite-verify: ensure-setup
	@$(BIN)/gauntlet verify --suites $(SUITES)

suite-verify-run: ensure-setup
	@$(BIN)/gauntlet verify --suites $(SUITES) --run

# Every suite's tests import a package literally named `suite`, so two suites
# cannot share one pytest process. Each gets its own, rooted at its directory.
suite-test: ensure-setup
	@for tests in $(SUITES)/*/tests; do \
		test -d "$$tests" || continue; \
		suite=$$(dirname "$$tests"); \
		echo "==> $$(basename $$suite)"; \
		(cd "$$suite" && $(BIN)/pytest tests -q -p no:cacheprovider) || exit 1; \
	done

# ---------------------------------------------------------------------------
# Quality
# ---------------------------------------------------------------------------

.PHONY: check format format-check gauntlet-test lint test test-e2e typecheck verify

# Everything: the bundle builds, every check passes, every test runs, and each
# suite's conformance profile executes against the contract.
verify: frontend check test-e2e suite-verify-run

check: format-check lint typecheck gauntlet-test suite-test frontend-check

# Every test in the project, and nothing else.
test: gauntlet-test suite-test frontend-test test-e2e

format: ensure-setup
	@$(BIN)/ruff format $(SDK) $(APP) $(SUITES)
	@$(BIN)/ruff check --fix $(SDK) $(APP) $(SUITES)

format-check: ensure-setup
	@$(BIN)/ruff format --check $(SDK) $(APP) $(SUITES)

lint: ensure-setup
	@$(BIN)/ruff check $(SDK) $(APP) $(SUITES)

typecheck: ensure-setup
	@$(BIN)/mypy --config-file $(ROOT)/mypy.ini $(SDK)/src $(APP)/src $(SUITES)

gauntlet-test: ensure-setup
	@$(BIN)/pytest $(SDK)/tests $(APP)/tests -m "not e2e" \
		--cov=gauntlet --cov=gauntlet_sdk \
		--cov-report=term-missing:skip-covered \
		--cov-report=xml:$(ROOT)/build/coverage.xml \
		--junitxml=$(ROOT)/build/junit.xml

# Spawns the system_stats suite as a real subprocess and consumes its event
# stream, so it takes seconds rather than milliseconds and `gauntlet-test`
# leaves it out.
test-e2e: ensure-setup
	@$(BIN)/pytest $(APP)/tests -m e2e

# ---------------------------------------------------------------------------
# Docs
# ---------------------------------------------------------------------------

.PHONY: api-spec schemas

schemas: ensure-setup
	@$(BIN)/gauntlet schema

api-spec: ensure-setup
	@mkdir -p $(ROOT)/build
	@$(PY) -c "import json; from gauntlet.app import create_app; \
		print(json.dumps(create_app().openapi(), indent=2))" > $(ROOT)/build/openapi.json
	@echo "wrote build/openapi.json"

# ---------------------------------------------------------------------------
# Clean
# ---------------------------------------------------------------------------

.PHONY: clean distclean

clean:
	@rm -rf $(ROOT)/build $(FRONTEND_OUT)
	@find $(ROOT) -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@find $(ROOT) -name '*.egg-info' -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@rm -rf $(ROOT)/.pytest_cache $(ROOT)/.ruff_cache $(ROOT)/.mypy_cache $(ROOT)/.coverage

distclean: clean
	@rm -rf $(VENV) $(ROOT)/output
