#
# The library suite authors install. `make -C packages/gauntlet-sdk help`, or
# the sdk-* targets at the top level, which delegate here.
#

include $(dir $(lastword $(MAKEFILE_LIST)))../../common.mk

.DEFAULT_GOAL := help

.PHONY: build help

help:
	@echo "gauntlet-sdk"
	@echo ""
	@echo "    make build             build the wheel into dist/"

# The other two artifacts are whole applications; this one is a dependency. A
# suite author installs it into whatever environment their suite runs in, and
# needs nothing else from this repository to write a conforming suite.
build:
	@mkdir -p $(DIST)
	@uv build --wheel --out-dir $(DIST) $(SDK)
