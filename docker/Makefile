#
# The server image. `make -C docker help`, or the docker-* targets at the top
# level, which delegate here.
#

include $(dir $(lastword $(MAKEFILE_LIST)))../common.mk

.DEFAULT_GOAL := help

IMAGE ?= gauntlet:latest
NAME  ?= gauntlet

.PHONY: build help run save stop

# The version the image is named for, taken from the application it carries so
# that the tarball and the desktop installers cannot disagree.
VERSION = $(shell sed -n 's/^version = "\(.*\)"/\1/p' $(APP)/pyproject.toml | head -1)

help:
	@echo "Gauntlet server image"
	@echo ""
	@echo "    make build             build the image"
	@echo "    make run               run it (port $(DOCKER_PORT))"
	@echo "    make save              write the image to dist/ as a tarball"
	@echo "    make stop              stop and remove it"

# The context is the repository: the build needs both packages, the frontend
# and the ui-kit. .dockerignore stays at the root because Docker reads it from
# the root of the context, not from beside the Dockerfile.
build:
	@docker build -f $(DOCKER)/Dockerfile -t $(IMAGE) $(ROOT)

run: build
	@echo "Gauntlet   http://localhost:$(DOCKER_PORT)"
	@docker run --rm --name $(NAME) \
		-p $(DOCKER_PORT):7100 \
		-v gauntlet-data:/data \
		$(IMAGE)

# What a machine without this repository needs: `docker load` reads it back.
save: build
	@mkdir -p $(DIST)
	@docker save $(IMAGE) | gzip > $(DIST)/gauntlet-$(VERSION)-image.tar.gz
	@echo "dist/gauntlet-$(VERSION)-image.tar.gz"

# `docker rm -f` succeeds against a container that is not there, so what was
# removed is decided before removing it rather than from the exit status.
stop:
	@if [ -n "$$(docker ps -aq --filter name=^/$(NAME)$$)" ]; then \
		docker rm -f $(NAME) >/dev/null; \
		echo "stopped $(NAME)"; \
	else \
		echo "$(NAME) is not running"; \
	fi
