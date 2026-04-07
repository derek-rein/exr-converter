# Versioning & releases for the monorepo (namespaced tags: exr_converter/v1.2.3, slate_maker/v0.4.0)
#
# Usage:
#   make help
#   make bump-exr PART=minor              # bump semver + sync APP_VERSION + uv lock (no git)
#   make release-exr PART=patch         # bump + lock + commit + tag
#   make release-exr PUSH=1             # … + git push branch + push tag (triggers CI release)

UV ?= uv
PYTHON ?= python3
BUMP := $(PYTHON) scripts/bump_app_version.py
PART ?= patch

.PHONY: help bump-exr release-exr

help:
	@echo "Monorepo version bump (semver x.y.z) and git tags"
	@echo ""
	@echo "  make bump-exr PART=patch|minor|major    # update pyproject, constants, uv.lock"
	@echo ""
	@echo "  make release-exr PART=patch             # bump + commit + tag (no push)"
	@echo "  make release-exr PUSH=1               # also: git push && push tag"
	@echo ""
	@echo "Current tags: git tag -l 'exr_converter/v*' --sort=-v:refname | head"

# --- bump only (no git) -------------------------------------------------------

bump-exr:
	@$(BUMP) bump exr_converter $(PART)
	@cd "$(CURDIR)" && $(UV) lock
	@echo "Done. Review diff, then: make release-exr PART=$(PART)  (or commit manually)"

# --- bump + commit + tag (+ optional push) -----------------------------------

define RELEASE_RULE
	@set -e; \
	cd "$(CURDIR)"; \
	$(BUMP) bump $(1) $(PART); \
	$(UV) lock; \
	eval $$($(BUMP) show $(1)); \
	git add apps/$(1)/pyproject.toml apps/$(1)/src/constants.py uv.lock; \
	if git diff --staged --quiet; then echo "No changes to commit."; exit 1; fi; \
	git commit -m "release($(1)): $${VERSION}"; \
	git tag "$${TAG}"; \
	echo "Created commit + tag $${TAG}"; \
	if [ "$(PUSH)" = "1" ]; then \
	  git push origin HEAD && git push origin "$${TAG}"; \
	  echo "Pushed branch and tag."; \
	else \
	  echo "Push when ready: git push origin HEAD && git push origin $${TAG}"; \
	fi
endef

release-exr:
	$(call RELEASE_RULE,exr_converter)
