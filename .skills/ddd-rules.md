# DDD & Modular Monolith Rules

## 4-Layer Separation (per bounded context)
```
src/<context>/
── domain/          # Pure Python. Entities, VOs, exceptions, Protocol ports. NO Flask/httpx/pydantic/dotenv.
── application/     # Use cases. Depends only on domain + ports.
├── infrastructure/  # DRF adapters, Pydantic schemas, mappers, repositories.
└── interfaces/      # Flask blueprints, ViewModels, Jinja2 templates.
```

## Dataclass Rules
- Entities: `@dataclass(slots=True)` (mutable).
- Value Objects: `@dataclass(frozen=True, slots=True)` (immutable).
- Use `str | None` and `list[X]` (Python 3.11+), not `Optional[X]` / `List[X]`.

## Port Patterns
- Declare with `typing.Protocol`.
- Methods are `async def` with `...` body.
- One port per aggregate boundary (e.g., `ShoppingListRepositoryPort`, `WishlistRepositoryPort`).
- `tests/integration/test_port_conformance.py` structurally asserts every adapter matches its port method-for-method (names, annotations, defaults, async-ness). When you add/change a port method, update the adapter in the same pass.

## Modular Monolith Boundaries
- **NO cross-context imports**: `src.budget` MUST NOT import from `src.tasks`, etc. Enforced by AST-based `test_context_boundaries.py`.
- `src/shared/` is the only allowed crossing point (for `DRFAPIClient`, `JWTCookieManager`, base exceptions, `Clock`).
- Each context has its own `User`/`UserId` NewType if needed — do not share domain types across contexts.
- New contexts mirror `src/purchases/` structure. Add to `app.py` wiring, register blueprint, add nav link.

## Enums
- Use `class Foo(str, Enum)` (NOT `StrEnum`) so members serialize straight to DRF JSON. Ruff `UP042` is suppressed via per-file ignores in `pyproject.toml`.
