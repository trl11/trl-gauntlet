#
# Gauntlet — test-suite runner.
#
# Start here:
#   make setup    one-time: create .venv and install everything
#   make run      build the frontend and serve the app
#   make check    format + lint + type-check + test
#

SHELL := /bin/bash
.DEFAULT_GOAL := help

ROOT         := $(abspath $(CURDIR))
VENV         := $(ROOT)/.venv
PY           := $(VENV)/bin/python
BIN          := $(VENV)/bin
APP          := $(ROOT)/packages/gauntlet
SDK          := $(ROOT)/packages/gauntlet-sdk
SUITES       := $(ROOT)/suites
FRONTEND     := $(ROOT)/frontend
FRONTEND_OUT := $(APP)/src/gauntlet/web_dist

# The interpreter the venv is built from. Resolved to an absolute path and
# pinned, because `uv venv --python python3` consults uv's own discovery order
# and will prefer a uv-managed interpreter over the one on PATH, quietly
# building against a different minor version than the pip fallback would.
# An activated venv puts its own python3 first and is about to be replaced,
# so drop it from the search.
SYS_PYTHON := $(shell PATH="$$(printf '%s' "$$PATH" | tr ':' '\n' \
	| grep -vxF '$(VENV)/bin' | paste -sd: -)" command -v python3)

# uv is much faster, and builds a venv without the python3-venv system package,
# which a stock Debian or Ubuntu Python does not ship. Fall back to pip and the
# stdlib so a bare Python still works.
UV := $(shell command -v uv 2>/dev/null)
ifeq ($(UV),)
  PIP_INSTALL = $(PY) -m pip install
  VENV_CREATE = $(SYS_PYTHON) -m venv $(VENV) && $(PY) -m pip install --quiet --upgrade pip
else
  PIP_INSTALL = $(UV) pip install --python $(PY)
  VENV_CREATE = $(UV) venv --seed --python $(SYS_PYTHON) $(VENV)
endif

# The frontend is optional: a Python-only environment has no npm, and `check`
# skips the frontend targets rather than failing there.
NPM := $(shell command -v npm 2>/dev/null)

PORT ?= 7100
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
	@echo "    make setup            create .venv and install both packages (editable)"
	@echo "    make clean            remove build artifacts and caches"
	@echo "    make distclean        also remove .venv and output/"
	@echo ""
	@echo "  Devcontainer"
	@echo "    make dev              start the devcontainer and open a shell"
	@echo "    make dev-stop         stop and remove the devcontainer"
	@echo "    make dev-status       show whether the devcontainer is running"
	@echo ""
	@echo "  Develop"
	@echo "    make run              build the frontend and serve (port $(PORT))"
	@echo "    make serve            the same, with auto-reload"
	@echo "    make stop             stop what run or serve started"
	@echo "    make frontend         build the frontend bundle"
	@echo "    make frontend-dev     frontend dev server with hot reload"
	@echo "    make frontend-check   lint and test the frontend"
	@echo ""
	@echo "  Suites"
	@echo "    make new-suite NAME=x scaffold a suite (TEMPLATE=python|shell)"
	@echo "    make templates        list the available suite templates"
	@echo "    make list             list discovered suites"
	@echo "    make verify           check every suite against the contract"
	@echo "    make verify-run       ...and execute each conformance profile"
	@echo ""
	@echo "  Quality"
	@echo "    make check            format-check + lint + typecheck + test + frontend-check"
	@echo "    make format           auto-format"
	@echo "    make lint             ruff"
	@echo "    make typecheck        mypy"
	@echo "    make test             pytest with coverage"
	@echo "    make test-e2e         drive a real suite run end to end"
	@echo "    make test-suites      each suite's own tests"
	@echo ""
	@echo "  Docs"
	@echo "    make schemas          print contract schema names"
	@echo "    make api-spec         write the OpenAPI spec to build/openapi.json"

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

$(PY):
	@echo "==> creating venv at $(VENV)"
	@$(VENV_CREATE)

# The devcontainer bind-mounts the repository, so the host and the container
# share .venv but reach it by different absolute paths. Every console script in
# bin/ carries one in its shebang, so a virtualenv built on one side cannot run
# on the other, and reinstalling only this project leaves the tools it did not
# touch broken. Record the path it was built for and start over when it moves.
VENV_ROOT_STAMP := $(VENV)/.gauntlet-root

.PHONY: setup
setup: ## Create the venv and install both packages editable
	@if [ -d $(VENV) ] && [ "$$(cat $(VENV_ROOT_STAMP) 2>/dev/null)" != "$(ROOT)" ]; then \
		echo "==> .venv was built for another path, recreating it"; \
		rm -rf $(VENV); \
	fi
	@$(MAKE) --no-print-directory $(PY)
	@echo "==> installing gauntlet-sdk (editable)"
	@$(PIP_INSTALL) -q -e "$(SDK)[dev]"
	@echo "==> installing gauntlet (editable)"
	@$(PIP_INSTALL) -q -e "$(APP)[dev]"
	@echo "$(ROOT)" > $(VENV_ROOT_STAMP)
	@echo ""
	@echo "Ready. Next: make run"

.PHONY: ensure-setup
ensure-setup:
	@{ test -x $(BIN)/gauntlet && \
	   test "$$(cat $(VENV_ROOT_STAMP) 2>/dev/null)" = "$(ROOT)"; } \
		|| $(MAKE) --no-print-directory setup

# ---------------------------------------------------------------------------
# Develop
# ---------------------------------------------------------------------------

# The app serves whatever bundle is on disk and falls back to a link to /docs
# when there is none, so building here is a convenience, not a requirement.
.PHONY: frontend-bundle
# Always rebuilds, so `make run` alone is enough after any change. Without npm
# the bundle cannot be built and the app serves the API only.
frontend-bundle:
	@if [ -n "$(NPM)" ]; then \
		$(MAKE) --no-print-directory frontend; \
	else \
		echo "npm is not installed; serving the API without the frontend"; \
	fi

.PHONY: run
run: ensure-setup frontend-bundle ## Set up, build the frontend, and serve
	@echo "Gauntlet   http://$(if $(filter 0.0.0.0,$(HOST)),localhost,$(HOST)):$(PORT)"
	@$(BIN)/gauntlet serve --host $(HOST) --port $(PORT)

.PHONY: serve
serve: ensure-setup frontend-bundle ## Serve with auto-reload, for working on the app
	@$(BIN)/gauntlet serve --host $(HOST) --port $(PORT) --reload

# Kills the process group, so a reloader and any suite the run spawned go with
# the server rather than being left behind. Run artifacts are kept; only the
# scratch profiles written for inline-profile runs are removed.
.PHONY: stop
stop: ## Stop what `make run` or `make serve` started, and clear its scratch files
	@own=$$(ps -o pgid= -p $$$$ | tr -d ' '); \
	stopped=0; \
	for port in $(PORT) $(FRONTEND_PORT); do \
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
	[ "$$stopped" = "1" ] || echo "nothing was listening on $(PORT) or $(FRONTEND_PORT)"
	@if [ -x $(PY) ]; then \
		runs=$$($(PY) -c "from gauntlet.config import load_settings; print(load_settings().runs_dir)" 2>/dev/null); \
		if [ -n "$$runs" ] && [ -d "$$runs/_scratch" ]; then \
			rm -rf "$$runs/_scratch"; \
			echo "removed $$runs/_scratch"; \
		fi; \
	fi

.PHONY: frontend-install
frontend-install:
	@test -n "$(NPM)" || { echo "npm is not installed"; exit 2; }
	@test -d $(FRONTEND)/node_modules || \
		(cd $(FRONTEND) && npm install --no-audit --no-fund --loglevel=warn)

.PHONY: frontend
frontend: frontend-install ## Build the frontend bundle into the app package
	@cd $(FRONTEND) && npm run build

.PHONY: frontend-dev
frontend-dev: frontend-install ## Frontend dev server on 7101, proxying /api to $(PORT)
	@cd $(FRONTEND) && npm run dev

.PHONY: frontend-check
frontend-check: frontend-install ## Format-check, lint and test the frontend
	@cd $(FRONTEND) && npm run format-check && npm run lint && npm run test

# ---------------------------------------------------------------------------
# Suites
# ---------------------------------------------------------------------------

.PHONY: new-suite
new-suite: ensure-setup ## Scaffold a suite: make new-suite NAME=my_suite [TEMPLATE=python|shell]
	@test -n "$(NAME)" || { echo "usage: make new-suite NAME=my_suite [TEMPLATE=python|shell]"; exit 2; }
	@$(BIN)/gauntlet new-suite $(NAME) --template $(or $(TEMPLATE),python) --into $(SUITES)

.PHONY: templates
templates: ensure-setup ## List the suite templates `make new-suite` can render
	@$(BIN)/gauntlet templates

.PHONY: list
list: ensure-setup ## List discovered suites
	@$(BIN)/gauntlet list --suites $(SUITES)

.PHONY: verify
verify: ensure-setup ## Static contract checks for every suite
	@$(BIN)/gauntlet verify --suites $(SUITES)

.PHONY: verify-run
verify-run: ensure-setup ## Contract checks including a real run of each conformance profile
	@$(BIN)/gauntlet verify --suites $(SUITES) --run

# ---------------------------------------------------------------------------
# Quality
# ---------------------------------------------------------------------------

.PHONY: check
check: format-check lint typecheck test test-suites ## Everything CI runs
	@if [ -n "$(NPM)" ]; then \
		$(MAKE) --no-print-directory frontend-check; \
	else \
		echo "==> skipping frontend checks: npm is not installed"; \
	fi

.PHONY: format
format: ensure-setup ## Auto-format
	@$(BIN)/ruff format $(SDK) $(APP) $(SUITES)
	@$(BIN)/ruff check --fix $(SDK) $(APP) $(SUITES)

.PHONY: format-check
format-check: ensure-setup ## Verify formatting
	@$(BIN)/ruff format --check $(SDK) $(APP) $(SUITES)

.PHONY: lint
lint: ensure-setup ## Lint
	@$(BIN)/ruff check $(SDK) $(APP) $(SUITES)

.PHONY: typecheck
typecheck: ensure-setup ## Type-check both packages and every suite
	@$(BIN)/mypy --config-file $(ROOT)/mypy.ini $(SDK)/src $(APP)/src $(SUITES)

.PHONY: test
test: ensure-setup ## Run tests with coverage
	@$(BIN)/pytest $(SDK)/tests $(APP)/tests -m "not e2e" \
		--cov=gauntlet --cov=gauntlet_sdk \
		--cov-report=term-missing:skip-covered \
		--cov-report=xml:$(ROOT)/build/coverage.xml \
		--junitxml=$(ROOT)/build/junit.xml

# Spawns the system_stats suite as a real subprocess and consumes its event
# stream, so it takes seconds rather than milliseconds and `test` leaves it out.
.PHONY: test-e2e
test-e2e: ensure-setup ## Run the end-to-end test against a real suite
	@$(BIN)/pytest $(APP)/tests -m e2e

# Every suite's tests import a package literally named `suite`, so two suites
# cannot share one pytest process. Each gets its own, rooted at its directory.
.PHONY: test-suites
test-suites: ensure-setup ## Run each suite's own tests
	@for tests in $(SUITES)/*/tests; do \
		test -d "$$tests" || continue; \
		suite=$$(dirname "$$tests"); \
		echo "==> $$(basename $$suite)"; \
		(cd "$$suite" && $(BIN)/pytest tests -q -p no:cacheprovider) || exit 1; \
	done

# ---------------------------------------------------------------------------
# Docs
# ---------------------------------------------------------------------------

.PHONY: schemas
schemas: ensure-setup ## List contract schema names
	@$(BIN)/gauntlet schema

.PHONY: api-spec
api-spec: ensure-setup ## Write the OpenAPI spec
	@mkdir -p $(ROOT)/build
	@$(PY) -c "import json; from gauntlet.app import create_app; \
		print(json.dumps(create_app().openapi(), indent=2))" > $(ROOT)/build/openapi.json
	@echo "wrote build/openapi.json"

# ---------------------------------------------------------------------------
# Clean
# ---------------------------------------------------------------------------

.PHONY: clean
clean: ## Remove build artifacts and caches
	@rm -rf $(ROOT)/build $(FRONTEND_OUT)
	@find $(ROOT) -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@find $(ROOT) -name '*.egg-info' -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@rm -rf $(ROOT)/.pytest_cache $(ROOT)/.ruff_cache $(ROOT)/.mypy_cache $(ROOT)/.coverage

.PHONY: distclean
distclean: clean ## Also remove the venv and run output
	@rm -rf $(VENV) $(ROOT)/output
