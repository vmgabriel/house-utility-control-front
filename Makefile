# Every target runs inside the `bff` container, so a developer's host Python is
# never involved and the suite cannot pass locally on a version the image does
# not have. `hatch` still does the work inside; only the entrypoint moved.
#
# `docker compose run` starts a *new* one-shot container rather than using the
# running one, so a stopped stack does not block `make lint` or `make test`.

.PHONY: setup run stop logs test lint format clean test-e2e test-e2e-headed \
        test-all shell

COMPOSE := docker compose

setup:
	$(COMPOSE) build

# The BFF publishes 5001, not 5000. Port 5000 belongs to the `caddy` service,
# which sits behind the `proxy` profile and is off by default:
#     $(COMPOSE) --profile proxy up -d
run:
	$(COMPOSE) up -d bff
	@echo "BFF running at http://localhost:5001"

# `stop` keeps volumes and the built image, so `make run` after a `make stop` is
# instant. `clean` is the destructive one.
stop:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

# Unit + integration. Fast, no browser, DRF faked with respx.
test:
	$(COMPOSE) run --rm bff hatch run pytest tests/unit tests/integration -v

# End-to-end against a real browser. Needs Playwright's Chromium in the image;
# `make setup` installs it (see the Dockerfile).
test-e2e:
	$(COMPOSE) run --rm bff hatch run pytest tests/e2e -v

test-e2e-headed:
	$(COMPOSE) run --rm bff hatch run pytest tests/e2e -v --headed

# Two pytest processes on purpose -- Playwright's sync API holds the event loop
# for the whole session, which breaks pytest-asyncio's auto mode in the async
# suites. See AGENTS.md, "Pytest Async Isolation".
test-all:
	$(MAKE) test
	$(MAKE) test-e2e

lint:
	$(COMPOSE) run --rm bff hatch run ruff check src tests
	$(COMPOSE) run --rm bff hatch run black --check src tests

format:
	$(COMPOSE) run --rm bff hatch run black src tests
	$(COMPOSE) run --rm bff hatch run ruff check --fix src tests

shell:
	$(COMPOSE) exec bff bash

clean:
	$(COMPOSE) down --volumes
	find . -type d -name "__pycache__" -exec rm -r {} +
	find . -type d -name "*.egg-info" -exec rm -r {} +
	rm -rf .pytest_cache .ruff_cache build/