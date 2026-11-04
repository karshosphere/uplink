# Project tasks. CI runs format-dry-run, build, test and release.

PACKAGE := uplink

# CI passes the version it releases; without it, setuptools-scm reads the tags
ifdef VERSION
export SETUPTOOLS_SCM_PRETEND_VERSION := $(VERSION)
endif

.PHONY: build format format-dry-run test release

build:
	uv sync --locked
	uv run --locked python -m compileall -q $(PACKAGE)

format:
	uv run --locked ruff format .

format-dry-run:
	uv run --locked ruff format --check .

test:
	uv run --locked pytest

release:
	rm -rf dist
	uv build --out-dir dist

# Host service tasks, run on the host rather than in a container.

SERVICE := uplink

.PHONY: start stop restart

start:
	deploy/install.sh

stop:
	systemctl --user stop $(SERVICE)

# stopped first, so the install never replaces the program while it is running
restart:
	systemctl --user is-active --quiet $(SERVICE) && systemctl --user stop $(SERVICE) || true
	deploy/install.sh
