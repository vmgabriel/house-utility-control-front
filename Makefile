.PHONY: setup format lint test test-e2e run clean

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

test-e2e:
	hatch run pytest tests/e2e -v

run:
	hatch run python -m src.interfaces.web.app

clean:
	find . -type d -name "__pycache__" -exec rm -r {} +
	find . -type d -name "*.egg-info" -exec rm -r {} +
	rm -rf .pytest_cache .ruff_cache
