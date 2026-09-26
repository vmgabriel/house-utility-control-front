.PHONY: setup format lint test test-e2e test-e2e-headed test-all run clean

setup:
	hatch env create
	hatch run playwright install chromium
	hatch run playwright install-deps

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
