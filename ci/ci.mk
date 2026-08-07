# CI targets: reproduce the Jenkins release workflow locally in the same
# disposable image. The source tree is bind-mounted, so dist/ and build/ remain
# available for inspection after the container exits.

CI_DIR := $(ROOT)/ci
CI_IMAGE := ci-trl-gauntlet
CI_TAG ?= local
CI_APT_PROXY_URL ?=
CI_DOCKER_GID := $(shell stat -c %g /var/run/docker.sock 2>/dev/null || echo 999)
CI_DOCKER_BIN := $(shell command -v docker)
CI_DOCKER_LIBEXEC := /usr/libexec/docker
CI_USER := $(shell id -u):$(shell id -g)
# Docker resolves a bind source on the host. Inside Gauntlet's devcontainer,
# GAUNTLET_HOST_WORKSPACE is that source; elsewhere the checkout path is valid.
CI_HOST_PATH := $(if $(GAUNTLET_HOST_WORKSPACE),$(GAUNTLET_HOST_WORKSPACE),$(ROOT))
CI_DOCKER_RUN := docker run --rm \
	--user $(CI_USER) \
	--group-add $(CI_DOCKER_GID) \
	-v /var/run/docker.sock:/var/run/docker.sock \
	-v $(CI_DOCKER_BIN):$(CI_DOCKER_BIN):ro \
	-v $(CI_DOCKER_LIBEXEC):$(CI_DOCKER_LIBEXEC):ro \
	-v $(CI_HOST_PATH):/workspace \
	-w /workspace \
	-e CI=true \
	-e HOME=/workspace \
	$(CI_IMAGE):$(CI_TAG)

.PHONY: ci-image ci-test ci-validate-dist ci-clean

## Build the local image that Jenkins uses for tests and release artifacts.
ci-image:
	@DOCKER_BUILDKIT=1 docker build \
		--build-arg APT_PROXY_URL=$(CI_APT_PROXY_URL) \
		--file $(CI_DIR)/Dockerfile \
		--tag $(CI_IMAGE):$(CI_TAG) \
		$(ROOT)
	@echo "CI image ready: $(CI_IMAGE):$(CI_TAG)"

## Run the full Jenkins workflow locally: setup, checks, release build, and artifact contract.
ci-test: ci-image
	@$(MAKE) --no-print-directory ci-run-distclean
	@$(MAKE) --no-print-directory ci-run-setup
	@$(MAKE) --no-print-directory ci-run-check
	@$(MAKE) --no-print-directory ci-run-build
	@$(MAKE) --no-print-directory ci-run-ci-validate-dist
	@echo "Local CI workflow completed"

## Run any Make target inside the CI image, e.g. `make ci-run-build`.
ci-run-%:
	@$(CI_DOCKER_RUN) bash -c 'make $*'

# This is the release contract Jenkins enforces before it archives dist/.
ci-validate-dist:
	@set -euo pipefail; \
	version=$$(< VERSION); \
	expected=( \
		"dist/gauntlet-$${version}.AppImage" \
		"dist/gauntlet-$${version}.deb" \
		"dist/gauntlet-$${version}-image.tar.gz" \
		"dist/gauntlet-$${version}-py3-none-any.whl" \
		"dist/gauntlet_sdk-$${version}-py3-none-any.whl" \
		"dist/README.txt" \
		"dist/setup-host.sh" \
		"dist/99-gauntlet-instruments.rules" \
	); \
	for artifact in "$${expected[@]}"; do test -s "$$artifact"; done; \
	test "$$(find dist -maxdepth 1 -type f | wc -l)" -eq "$${#expected[@]}"; \
	printf 'Verified release artifacts:\n'; \
	printf '  %s\n' "$${expected[@]}"

## Remove the local CI image.
ci-clean:
	@docker rmi $(CI_IMAGE):$(CI_TAG) 2>/dev/null || true
