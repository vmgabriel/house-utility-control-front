.PHONY: setup format lint test test-e2e test-e2e-headed test-all run clean \
        sync-api-contract verify-api-contract

# --- API contract management ------------------------------------------------
# src/contract/types.py is a DRIFT-DETECTION artifact, not a runtime type
# source. The mappers keep using the hand-written pydantic schemas in the
# bounded contexts, because drf-spectacular marks several fields required and
# non-nullable that the live API actually returns as null (see
# src/contract/overrides.py). Feeding the generated types to the mappers would
# 500 the dashboard for any user with no transactions.
#
# The DRF schema lives at the project root of the API, *outside* the versioned
# prefix: DRF_API_BASE_URL is .../api/v1 while the schema is .../api/schema/.
# Override when the backend is not on localhost, e.g.
#   make verify-api-contract SCHEMA_URL=http://10.0.0.5:8000/api/schema/
SCHEMA_URL ?= http://localhost:8000/api/schema/
# Staging copy of the schema. Kept in build/ (already gitignored) rather than
# /tmp so the fetch is inspectable and reproducible per working tree.
SCHEMA_FILE ?= build/openapi.json
CONTRACT_FILE ?= src/contract/types.py
# Scratch file that `verify-api-contract` compares the committed contract
# against. Never committed.
CONTRACT_CHECK_FILE ?= build/types_check.py

# The header is emitted verbatim, so sync and verify must pass byte-identical
# flags; otherwise verify reports drift on every run. Keep both targets on
# CODEGEN_FLAGS. The header text lives in a real file rather than inline in the
# Makefile because a multi-line value containing `#` cannot survive make's
# comment parsing, and command substitution preserves its internal newlines.
CONTRACT_HEADER_FILE := src/contract/HEADER.txt
CODEGEN_FLAGS := --input-file-type openapi \
	--output-model-type dataclasses.dataclass \
	--custom-file-header "$$(cat $(CONTRACT_HEADER_FILE))"

setup:
	hatch env create
	hatch run playwright install chromium
	hatch run playwright install-deps

# Fetches the schema. The endpoint defaults to YAML (Content-Type:
# application/vnd.oai.openapi), so the Accept header is what forces JSON here;
# without it a file named .json holds YAML and the generator aborts.
define fetch_schema
	@mkdir -p $(dir $(1))
	curl -sfS -H 'Accept: application/json' $(SCHEMA_URL) -o $(1)
	hatch run python -c "import json; json.load(open('$(1)'))" \
		|| { echo "ERROR: $(1) is not valid JSON; is SCHEMA_URL correct?"; exit 1; }
endef

# Generate + format. Formatting must happen on both paths or the diff in
# verify compares formatted output against raw output and always differs.
define generate_contract
	hatch run datamodel-codegen --input $(1) $(CODEGEN_FLAGS) --output $(2)
	hatch run ruff check --fix $(2)
	hatch run black -q $(2)
endef

# Regenerates src/contract/types.py from the backend's OpenAPI schema.
sync-api-contract:
	$(call fetch_schema,$(SCHEMA_FILE))
	$(call generate_contract,$(SCHEMA_FILE),$(CONTRACT_FILE))
	@echo "Contract regenerated: $(CONTRACT_FILE)"

# CI drift check: regenerate into a scratch file and diff against the committed
# contract. Fails when the backend's API changed without the contract being
# regenerated, which is the signal to review and run sync-api-contract.
verify-api-contract:
	$(call fetch_schema,$(CONTRACT_CHECK_FILE).schema.json)
	$(call generate_contract,$(CONTRACT_CHECK_FILE).schema.json,$(CONTRACT_CHECK_FILE))
	@if diff -q $(CONTRACT_FILE) $(CONTRACT_CHECK_FILE) >/dev/null 2>&1; then \
		echo "OK: API contract is in sync with $(SCHEMA_URL)"; \
		rm -f $(CONTRACT_CHECK_FILE) $(CONTRACT_CHECK_FILE).schema.json; \
	else \
		echo ""; \
		echo "ERROR: API contract has drifted from $(SCHEMA_URL)"; \
		echo "Review the differences below, then run 'make sync-api-contract'"; \
		echo "and commit the result if the API change was intentional."; \
		echo ""; \
		diff -u $(CONTRACT_FILE) $(CONTRACT_CHECK_FILE) || true; \
		rm -f $(CONTRACT_CHECK_FILE) $(CONTRACT_CHECK_FILE).schema.json; \
		exit 1; \
	fi

format:
	hatch run black src tests
	hatch run ruff check --fix src tests

lint:
	hatch run ruff check src tests
	hatch run black --check src tests

test:
	hatch run pytest tests/unit tests/integration -v

# End-to-end tests run headless against a real Flask server and a fake DRF
# backend, both started by the fixtures in tests/e2e/conftest.py.
test-e2e:
	hatch run pytest tests/e2e -v

# Same suite with a visible browser, for debugging a failing journey.
test-e2e-headed:
	hatch run pytest tests/e2e -v --headed

# Everything: unit, integration and end-to-end. These run as two pytest
# processes on purpose -- Playwright's sync API holds the event loop for the
# whole session, which breaks pytest-asyncio's auto mode in the async suites.
test-all:
	$(MAKE) test
	$(MAKE) test-e2e

run:
	hatch run python -m src.interfaces.web.app

clean:
	find . -type d -name "__pycache__" -exec rm -r {} +
	find . -type d -name "*.egg-info" -exec rm -r {} +
	rm -rf .pytest_cache .ruff_cache
