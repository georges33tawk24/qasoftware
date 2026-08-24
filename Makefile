PY    ?= python3.12
VENV  := .venv
BIN   := $(VENV)/bin
STAMP := $(VENV)/.installed

.PHONY: install test lint fmt clean web dogfood

$(STAMP): pyproject.toml
	$(PY) -m venv $(VENV)
	$(BIN)/python -m pip install -q --upgrade pip
	$(BIN)/python -m pip install -q -e ".[dev]"
	@touch $@

install: $(STAMP)

test: $(STAMP)
	$(BIN)/pytest

lint: $(STAMP)
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .
	$(BIN)/mypy

fmt: $(STAMP)
	$(BIN)/ruff format .
	$(BIN)/ruff check --fix .

web:
	cd apps/web && [ -d node_modules ] || npm ci
	cd apps/web && npm run lint
	cd apps/web && NEXT_PUBLIC_API_ORIGIN=$(DOGFOOD_API) npm run build

# Bureau checked by Bureau. The origin is inlined by `next build`, so the build and the
# test have to agree on the port — hence one variable, used twice.
DOGFOOD_API ?= http://127.0.0.1:8099

dogfood: web $(STAMP)
	$(BIN)/pytest tests/test_ui.py -q

clean:
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

vendor:
	npm pack axe-core@4.13.0 >/dev/null
	tar -xzf axe-core-4.13.0.tgz package/axe.min.js
	mkdir -p vendor && mv package/axe.min.js vendor/axe.min.js
	rm -rf package axe-core-4.13.0.tgz

browsers: $(STAMP)
	$(BIN)/playwright install chromium

.PHONY: vendor browsers
