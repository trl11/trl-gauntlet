#
# Gauntlet — test-suite runner.
#
# Start here:
#   make setup    one-time: create .venv and install everything
#   make run      run the app with auto-reload
#   make check    format + lint + type-check + test
#

SHELL := /bin/bash
.DEFAULT_GOAL := help

ROOT      := $(abspath $(CURDIR))
VENV      := $(ROOT)/.venv
PY        := $(VENV)/bin/python
BIN       := $(VENV)/bin
APP       := $(ROOT)/packages/gauntlet
SDK       := $(ROOT)/packages/gauntlet-suite
SUITES    := $(ROOT)/suites
WEB       := $(ROOT)/web
WEB_OUT   := $(APP)/src/gauntlet/web_dist

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

PORT ?= 7100
HOST ?= 127.0.0.1

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
	@echo "    make run              run the app with auto-reload (port $(PORT))"
	@echo "    make serve            run the app without reload"
	@echo "    make web              build the frontend bundle"
	@echo "    make web-dev          frontend dev server with hot reload"
	@echo ""
	@echo "  Suites"
	@echo "    make new-suite NAME=x scaffold a suite (TEMPLATE=python|shell)"
	@echo "    make templates        list the available suite templates"
	@echo "    make list             list discovered suites"
	@echo "    make verify           check every suite against the contract"
	@echo "    make verify-run       ...and execute each conformance profile"
	@echo ""
	@echo "  Quality"
	@echo "    make check            format-check + lint + typecheck + test"
	@echo "    make format           auto-format"
	@echo "    make lint             ruff"
	@echo "    make typecheck        mypy"
	@echo "    make test             pytest with coverage"
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
	@echo "==> installing gauntlet-suite (editable)"
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

.PHONY: run
run: ensure-setup ## Run with auto-reload
	@$(BIN)/gauntlet serve --host $(HOST) --port $(PORT) --reload

.PHONY: serve
serve: ensure-setup ## Run without reload
	@$(BIN)/gauntlet serve --host $(HOST) --port $(PORT)

.PHONY: web
web: ## Build the frontend bundle
	@if [ ! -d "$(WEB)" ]; then \
		echo "No web/ directory yet. The API is at http://$(HOST):$(PORT)/docs"; \
	else \
		cd $(WEB) && npm install --no-audit --no-fund --loglevel=warn && npm run build; \
	fi

.PHONY: web-dev
web-dev: ## Frontend dev server with hot reload
	@if [ ! -d "$(WEB)" ]; then \
		echo "No web/ directory yet."; \
		exit 1; \
	fi
	@cd $(WEB) && npm run dev

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
check: format-check lint typecheck test ## Everything CI runs

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
typecheck: ensure-setup ## Type-check both packages
	@$(BIN)/mypy --config-file $(ROOT)/mypy.ini $(SDK)/src $(APP)/src

.PHONY: test
test: ensure-setup ## Run tests with coverage
	@$(BIN)/pytest $(SDK)/tests $(APP)/tests \
		--cov=gauntlet --cov=gauntlet_suite \
		--cov-report=term-missing:skip-covered \
		--cov-report=xml:$(ROOT)/build/coverage.xml \
		--junitxml=$(ROOT)/build/junit.xml

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
	@rm -rf $(ROOT)/build $(WEB_OUT)
	@find $(ROOT) -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@find $(ROOT) -name '*.egg-info' -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@rm -rf $(ROOT)/.pytest_cache $(ROOT)/.ruff_cache $(ROOT)/.mypy_cache $(ROOT)/.coverage

.PHONY: distclean
distclean: clean ## Also remove the venv and run output
	@rm -rf $(VENV) $(ROOT)/output
