# Budget Tracker Frontend

SSR Frontend BFF for Budget Tracker using Flask, Jinja2, and Clean Architecture.

## Stack

- **Framework**: Flask + Jinja2
- **Styling / interactivity**: Tailwind CSS (CDN) + Alpine.js
- **HTTP client**: `httpx`
- **Validation**: `pydantic`
- **Tooling**: Hatch, Makefile, ruff, black
- **Testing**: pytest, pytest-asyncio, playwright, respx

## Architecture

```text
src/
├── domain/          # Pure Python: entities, value objects, protocols
├── application/     # Use cases
├── infrastructure/  # Adapters: DRF API client, secure cookie manager
├── interfaces/      # Flask app, blueprints, Jinja2 templates
└── shared/          # Cross-cutting: Clock port, common exceptions
```

Dependencies point inward: `interfaces` -> `application` -> `domain`.
`domain` has no framework imports.

## Getting started

```bash
cp .env.example .env
make setup
make run      # http://localhost:5000
```

## Quality & tests

```bash
make lint
make format
make test        # unit + integration
make test-e2e    # playwright
make clean
```
