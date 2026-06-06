# ============================================================
# HLS Plugin - Makefile
# ============================================================

PLUGIN_NAME    := E2HLSServer
PACKAGE_NAME   := enigma2-plugin-extensions-e2hlsserver
VERSION := $(shell cat VERSION 2>/dev/null | tr -d '[:space:]')
ARCHITECTURE   := all

# Box connection
BOX_HOST       := 192.168.1.4
BOX_USER       := root
BOX_PORT       := 22
BOX_SSH        := ssh -p $(BOX_PORT) $(BOX_USER)@$(BOX_HOST)
BOX_SCP        := scp -P $(BOX_PORT)

# Paths
SRC_DIR        := src
PLUGIN_LOCALE_DIR := $(SRC_DIR)/$(PLUGIN_NAME)/locale
CONTROL_DIR    := control
BUILD_DIR      := build
IPK_STAGE      := $(BUILD_DIR)/ipk
PLUGIN_STAGE   := $(IPK_STAGE)/usr/lib/enigma2/python/Plugins/Extensions/$(PLUGIN_NAME)
CONTROL_STAGE  := $(IPK_STAGE)/CONTROL
IPK_FILE       := $(BUILD_DIR)/$(PACKAGE_NAME)_$(VERSION)_$(ARCHITECTURE).ipk

# Box plugin path
BOX_PLUGIN_DIR := /usr/lib/enigma2/python/Plugins/Extensions/$(PLUGIN_NAME)

# Source files (recursive, supports package subfolders)
PY_FILES       := $(shell find $(SRC_DIR) -name "*.py" 2>/dev/null)
JSON_FILES     := $(shell find $(SRC_DIR) -name "*.json" 2>/dev/null)
HTML_FILES     := $(shell find $(SRC_DIR) -name "*.html" 2>/dev/null)
LOCALE_FILES   := $(wildcard $(PLUGIN_LOCALE_DIR)/*/LC_MESSAGES/*.po)

.PHONY: all build deploy install clean logs logs-follow status check shell \
        check-deps help ipk compile-locale

# ============================================================
# Default
# ============================================================
all: help

# ============================================================
# Help
# ============================================================
help:
	@echo ""
	@echo "HLS Plugin Build System"
	@echo "========================"
	@echo ""
	@echo "  make build      - Build IPK package (in $(BUILD_DIR)/)"
	@echo "  make deploy     - Fast deploy: copy files directly to box (no IPK)"
	@echo "  make install    - Build IPK, copy to box and install via opkg"
	@echo "  make clean      - Remove build directory"
	@echo ""
	@echo "  make logs       - Show last 50 lines of plugin log"
	@echo "  make logs-follow- Follow plugin log live"
	@echo "  make status     - Show plugin status from box"
	@echo "  make shell      - Open SSH shell to box"
	@echo ""
	@echo "  make check-deps - Check if required tools are installed"
	@echo ""
	@echo "Box: $(BOX_USER)@$(BOX_HOST):$(BOX_PORT)"
	@echo ""

# ============================================================
# Check dependencies
# ============================================================
check-deps:
	@echo "Checking build dependencies..."
	@which ar      > /dev/null 2>&1 || (echo "ERROR: 'ar' not found (install binutils)"; exit 1)
	@which tar     > /dev/null 2>&1 || (echo "ERROR: 'tar' not found"; exit 1)
	@which gzip    > /dev/null 2>&1 || (echo "ERROR: 'gzip' not found"; exit 1)
	@which ssh     > /dev/null 2>&1 || (echo "ERROR: 'ssh' not found"; exit 1)
	@which scp     > /dev/null 2>&1 || (echo "ERROR: 'scp' not found"; exit 1)
	@which msgfmt  > /dev/null 2>&1 || echo "WARNING: 'msgfmt' not found (locale compilation skipped)"
	@which dos2unix > /dev/null 2>&1 || echo "WARNING: 'dos2unix' not found (using sed fallback)"
	@echo "OK"

# ============================================================
# Build IPK
# ============================================================
build: check-deps clean-build $(IPK_FILE)
	@echo ""
	@echo "========================================="
	@echo " Build complete: $(IPK_FILE)"
	@echo "========================================="

$(IPK_FILE): $(IPK_STAGE)
	@echo "Building IPK: $(IPK_FILE)..."
	@cd $(IPK_STAGE) && \
		tar czf ../../data.tar.gz --exclude=CONTROL . && \
		tar czf ../../control.tar.gz -C CONTROL . && \
		echo "1.0" > ../../debian-binary && \
		cd ../.. && \
		ar rcs $(notdir $(IPK_FILE)) debian-binary control.tar.gz data.tar.gz && \
		mv $(notdir $(IPK_FILE)) $(IPK_FILE) && \
		rm -f debian-binary control.tar.gz data.tar.gz
	@echo "IPK created: $(IPK_FILE) ($(shell du -sh $(IPK_FILE) | cut -f1))"

$(IPK_STAGE): $(PY_FILES) $(JSON_FILES) $(HTML_FILES) $(LOCALE_FILES)
	@echo "Staging files..."
	@mkdir -p $(PLUGIN_STAGE)
	@mkdir -p $(CONTROL_STAGE)

	# Python source files
	@cp $(SRC_DIR)/*.py   $(PLUGIN_STAGE)/
	@if [ -d $(SRC_DIR)/E2HLSServer ]; then \
		cp -r $(SRC_DIR)/E2HLSServer $(PLUGIN_STAGE)/; \
	fi
	@if ls $(SRC_DIR)/*.json > /dev/null 2>&1; then \
		cp $(SRC_DIR)/*.json $(PLUGIN_STAGE)/; \
	fi
	@cp $(SRC_DIR)/*.html $(PLUGIN_STAGE)/

	# Locale files (kept inside src/E2HLSServer like other plugins)
	@if [ -d $(PLUGIN_LOCALE_DIR) ]; then \
		cp -r $(PLUGIN_LOCALE_DIR) $(PLUGIN_STAGE)/locale; \
		$(MAKE) compile-locale PLUGIN_STAGE=$(PLUGIN_STAGE); \
	fi

	# CONTROL files
	@cp $(CONTROL_DIR)/control  $(CONTROL_STAGE)/
	@cp $(CONTROL_DIR)/postinst $(CONTROL_STAGE)/
	@cp $(CONTROL_DIR)/prerm    $(CONTROL_STAGE)/
	@chmod 755 $(CONTROL_STAGE)/postinst $(CONTROL_STAGE)/prerm

	# Update version in control file
	@sed -i 's/^Version:.*/Version: $(VERSION)/' $(CONTROL_STAGE)/control

	# Fix line endings (Windows -> Unix)
	@$(call fix_line_endings,$(PLUGIN_STAGE))
	@$(call fix_line_endings,$(CONTROL_STAGE))

	@echo "Staging complete: $(PLUGIN_STAGE)"

# Compile .po locale files to .mo
compile-locale:
	@if which msgfmt > /dev/null 2>&1; then \
		find $(PLUGIN_STAGE)/locale -name "*.po" | while read po; do \
			mo=$$(echo $$po | sed 's/\.po$$/.mo/'); \
			pyi18n=$$(dirname $$po)/de.pyi18n; \
			echo "  Compiling locale: $$po -> $$mo"; \
			msgfmt -o $$mo $$po; \
			echo "  Compiling locale: $$po -> $$pyi18n"; \
			msgfmt -o $$pyi18n $$po; \
		done; \
	else \
		echo "WARNING: msgfmt not found, skipping locale compilation"; \
	fi

# ============================================================
# Fix line endings helper
# ============================================================
define fix_line_endings
	@if which dos2unix > /dev/null 2>&1; then \
		find $(1) -type f \( -name "*.py" -o -name "*.sh" -o -name "*.json" -o -name "*.html" \) \
			-exec dos2unix {} \; 2>/dev/null; \
	else \
		find $(1) -type f \( -name "*.py" -o -name "*.sh" -o -name "*.json" -o -name "*.html" \) \
			-exec sed -i 's/\r//' {} \; 2>/dev/null; \
	fi
endef

# ============================================================
# Deploy (fast - direct file copy, no IPK)
# ============================================================
deploy:
	@echo ""
	@echo "========================================="
	@echo " Fast Deploy to $(BOX_USER)@$(BOX_HOST)"
	@echo "========================================="
	@echo "Creating plugin directory on box..."
	@$(BOX_SSH) "mkdir -p $(BOX_PLUGIN_DIR)"

	@echo "Copying source files..."
	@$(BOX_SCP) $(SRC_DIR)/*.py   $(BOX_USER)@$(BOX_HOST):$(BOX_PLUGIN_DIR)/
	@if [ -d $(SRC_DIR)/E2HLSServer ]; then \
		$(BOX_SCP) -r $(SRC_DIR)/E2HLSServer $(BOX_USER)@$(BOX_HOST):$(BOX_PLUGIN_DIR)/; \
	fi
	@if ls $(SRC_DIR)/*.json > /dev/null 2>&1; then \
		$(BOX_SCP) $(SRC_DIR)/*.json $(BOX_USER)@$(BOX_HOST):$(BOX_PLUGIN_DIR)/; \
	fi
	@$(BOX_SCP) $(SRC_DIR)/*.html $(BOX_USER)@$(BOX_HOST):$(BOX_PLUGIN_DIR)/

	@if [ -d $(SRC_DIR)/E2HLSServer/res ]; then \
		$(BOX_SCP) -r $(SRC_DIR)/E2HLSServer/res $(BOX_USER)@$(BOX_HOST):$(BOX_PLUGIN_DIR)/E2HLSServer/; \
	fi

	@echo "Removing old FakeE2HLSServer if present..."
	@$(BOX_SSH) "rm -rf /usr/lib/enigma2/python/Plugins/Extensions/FakeE2HLSServer" 2>/dev/null || true

	@echo "Clearing Python cache on box..."
	@$(BOX_SSH) "find $(BOX_PLUGIN_DIR) -name '*.pyc' -delete; \
	             find $(BOX_PLUGIN_DIR) -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null; \
	             true"

	@echo "Restarting Enigma2..."
	@$(BOX_SSH) "killall -HUP enigma2" 2>/dev/null || true

	@echo ""
	@echo "Deploy complete!"
	@echo ""

# ============================================================
# Install (build IPK + install on box)
# ============================================================
install: build
	@echo ""
	@echo "========================================="
	@echo " Installing plugin on box via IPK..."
	@echo "========================================="
	@echo "Target: $(BOX_USER)@$(BOX_HOST):$(BOX_PORT)"

	@echo "Copying IPK to box..."
	@$(BOX_SCP) $(IPK_FILE) $(BOX_USER)@$(BOX_HOST):/tmp/

	@echo "Installing via opkg..."
	@$(BOX_SSH) "opkg install --force-reinstall /tmp/$(notdir $(IPK_FILE)) && \
	             rm -f /tmp/$(notdir $(IPK_FILE))"

	@echo ""
	@echo "Installation complete!"
	@echo ""

# ============================================================
# Clean
# ============================================================
clean: clean-build
	@echo "Clean complete"

clean-build:
	@rm -rf $(BUILD_DIR)
	@mkdir -p $(BUILD_DIR)

# ============================================================
# Box utilities
# ============================================================
logs:
	@$(BOX_SSH) "tail -n 50 /tmp/fakehls/logs/plugin.log 2>/dev/null || \
	             cat $(shell $(BOX_SSH) 'cat /etc/enigma2/settings 2>/dev/null | \
	             grep e2hlsserver.hls_dir | cut -d= -f2' 2>/dev/null)/logs/plugin.log 2>/dev/null || \
	             echo 'No log file found'"

logs-follow:
	@$(BOX_SSH) "tail -f /tmp/fakehls/logs/plugin.log"

status:
	@echo "=== Box Status ==="
	@$(BOX_SSH) "echo '--- Enigma2:'; \
	             ps | grep enigma2 | grep -v grep | head -3; \
	             echo ''; \
	             echo '--- FFmpeg:'; \
	             ps | grep ffmpeg | grep -v grep | head -5; \
	             echo ''; \
	             echo '--- HLS Files:'; \
	             ls -lh /tmp/fakehls/*.ts /tmp/fakehls/*.m3u8 2>/dev/null | head -20 || echo 'none'; \
	             echo ''; \
	             echo '--- Disk /tmp:'; \
	             df -h /tmp; \
	             echo ''; \
	             echo '--- Plugin HTTP:'; \
	             wget -q -O - http://127.0.0.1:8080/status 2>/dev/null | head -20 || echo 'Server not responding'"

shell:
	@$(BOX_SSH)

apply:
	@$(BOX_SSH) \
	    "init 4 >/dev/null 2>&1 || killall -9 enigma2 >/dev/null 2>&1 || true; sleep 2; init 3 >/dev/null 2>&1 || true"

restart: apply
