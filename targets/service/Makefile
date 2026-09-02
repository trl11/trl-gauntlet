#
# The rig service. `make -C targets/service help`, or the service-* targets at
# the top level, which delegate here.
#

include $(dir $(lastword $(MAKEFILE_LIST)))../../common.mk

.DEFAULT_GOAL := help

PACKAGE := gauntlet-rig
STAGE   := $(ROOT)/build/service/$(VERSION)/$(PACKAGE)
DEB     := $(DIST)/$(PACKAGE)-$(VERSION).deb

# Where the package puts itself, and what the units are written to run.
PREFIX         := /opt/gauntlet
SERVE_GAUNTLET := $(PREFIX)/serve-gauntlet.sh
SERVE_PAGE     := $(PREFIX)/runtime/bin/python3 -s $(PREFIX)/homepage/serve-homepage.py
UNIT_DIR       := /usr/lib/systemd/user
UDEV_DIR       := /usr/lib/udev/rules.d
SYSCTL_DIR     := /usr/lib/sysctl.d
POLKIT_DIR     := /etc/polkit-1/localauthority/50-local.d

.PHONY: build help

help:
	@echo "Gauntlet rig service"
	@echo ""
	@echo "    make build             package the rig service as one deb in dist/"

# The runtime is the app's: a relocatable CPython with both wheels installed
# into it, and the frontend inside the one that carries it. Built there rather
# than again here, so the rig and the desktop bundle serve the same Gauntlet.
#
# That target always rebuilds, so `make build` at the top level pays for it
# twice — once for the AppImage, once here. The alternative is packaging
# whatever runtime the last build happened to leave behind, which is how a deb
# ends up shipping a Gauntlet nobody built.
build:
	@$(MAKE) --no-print-directory -C $(DESKTOP) runtime
	@rm -rf $(STAGE)
	@install -d $(STAGE)$(PREFIX) $(STAGE)$(UNIT_DIR) $(STAGE)$(UDEV_DIR) \
		$(STAGE)$(SYSCTL_DIR) $(STAGE)$(POLKIT_DIR) $(STAGE)/usr/share/doc/$(PACKAGE) $(STAGE)/DEBIAN
	@cp -a $(DESKTOP_RUNTIME) $(STAGE)$(PREFIX)/runtime
	@cp -a $(CAMPAIGNS) $(STAGE)$(PREFIX)/campaigns
	# What running the suites in the checkout leaves behind. All of it is
	# gitignored, so the copy above is the only way any of it gets shipped.
	@find $(STAGE)$(PREFIX)/campaigns -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@find $(STAGE)$(PREFIX)/campaigns \( -name "*.pyc" -o -name ".coverage" \) -delete 2>/dev/null || true
	@install -m 755 $(SERVE_SCRIPT) $(STAGE)$(PREFIX)/
	@install -d $(STAGE)$(PREFIX)/homepage
	@install -m 755 $(PAGE_SERVE) $(STAGE)$(PREFIX)/homepage/
	@install -m 644 $(PAGE_HTML) $(PAGE_BANNER) $(STAGE)$(PREFIX)/homepage/
	@install -m 644 $(UDEV_RULES) $(STAGE)$(UDEV_DIR)/
	@install -m 644 $(PORT_SYSCTL) $(STAGE)$(SYSCTL_DIR)/
	@install -m 644 $(POLKIT_RULES) $(STAGE)$(POLKIT_DIR)/
	@install -m 644 $(HOST_README) $(STAGE)/usr/share/doc/$(PACKAGE)/
	# The units carry a placeholder because where a bundle lands is only known
	# by whoever installs it. Here that is this package, so it is $(PREFIX).
	@sed "s|@SERVE@|$(SERVE_GAUNTLET)|" $(SERVICE_UNIT) > $(STAGE)$(UNIT_DIR)/$(notdir $(SERVICE_UNIT))
	@sed "s|@SERVE@|$(SERVE_PAGE)|" $(PAGE_UNIT) > $(STAGE)$(UNIT_DIR)/$(notdir $(PAGE_UNIT))
	@chmod 644 $(STAGE)$(UNIT_DIR)/*.service
	@install -m 755 $(SERVICE)/package/postinst $(STAGE)/DEBIAN/
	@sed -e "s|@VERSION@|$(VERSION)|" \
	     -e "s|@SIZE@|$$(du -sk $(STAGE) | cut -f1)|" \
	     $(SERVICE)/package/control > $(STAGE)/DEBIAN/control
	@mkdir -p $(DIST)
	# --root-owner-group rather than fakeroot: everything here is root's on the
	# far side anyway, and it is one fewer thing a build host has to have.
	@dpkg-deb --root-owner-group --build $(STAGE) $(DEB) >/dev/null
	@echo "dist/$(notdir $(DEB))"
