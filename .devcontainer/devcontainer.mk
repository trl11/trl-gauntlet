#
# Devcontainer lifecycle. Included from the top-level Makefile.
#
# Targets are no-ops when already inside the container, so `make dev` is safe
# to run from either side.
#

WORKSPACE_NAME    := gauntlet
DEVCONTAINER_DIR  := $(ROOT)/.devcontainer
DEVCONTAINER_FILTER = label=devcontainer.local_folder=$(ROOT)

ifeq ($(REBUILD),1)
REBUILD_FLAG := --build-no-cache
else
REBUILD_FLAG :=
endif

.PHONY: dev
dev: ## Start the devcontainer and open a shell in it
	@if [ "$(GAUNTLET_DEVCONTAINER)" = "true" ]; then \
		echo "Already inside the devcontainer. Use 'make run' to start the app."; \
		exit 0; \
	fi; \
	if ! command -v devcontainer >/dev/null 2>&1; then \
		echo "The devcontainer CLI is required: npm install -g @devcontainers/cli"; \
		exit 1; \
	fi; \
	if ! docker ps --filter "$(DEVCONTAINER_FILTER)" --format "{{.ID}}" | grep -q .; then \
		STOPPED_ID=$$(docker ps -a --filter "$(DEVCONTAINER_FILTER)" --format "{{.ID}}" | head -1); \
		if [ -n "$$STOPPED_ID" ] && [ "$(REBUILD)" != "1" ]; then \
			echo "Reusing devcontainer $$STOPPED_ID"; \
			docker start $$STOPPED_ID >/dev/null; \
		else \
			[ -n "$$STOPPED_ID" ] && docker rm -f $$STOPPED_ID >/dev/null || true; \
			echo "Starting devcontainer..."; \
			devcontainer up $(REBUILD_FLAG) \
				--workspace-folder $(ROOT) \
				--config $(DEVCONTAINER_DIR)/devcontainer.json || exit 1; \
		fi; \
	fi; \
	CONTAINER_ID=$$(docker ps --filter "$(DEVCONTAINER_FILTER)" --format "{{.ID}}" | head -1); \
	if [ -z "$$CONTAINER_ID" ]; then echo "Devcontainer failed to start"; exit 1; fi; \
	docker exec -it -u dev -w /workspaces/$(WORKSPACE_NAME) $$CONTAINER_ID bash

.PHONY: dev-stop
dev-stop: ## Stop and remove the devcontainer
	@CONTAINER_ID=$$(docker ps -a --filter "$(DEVCONTAINER_FILTER)" --format "{{.ID}}" | head -1); \
	if [ -n "$$CONTAINER_ID" ]; then \
		docker rm -f $$CONTAINER_ID >/dev/null; \
		echo "Devcontainer stopped and removed"; \
	else \
		echo "No devcontainer found"; \
	fi

.PHONY: dev-status
dev-status: ## Show whether the devcontainer is running
	@docker ps -a --filter "$(DEVCONTAINER_FILTER)" \
		--format "table {{.ID}}\t{{.Status}}\t{{.Image}}" | head -5
