"""The bounded contexts must not import each other.

AGENTS.md constraint 1: `src.budget` MUST NOT import `src.profile` or
`src.users`, and so on. Contexts communicate through the shared kernel or the
external API, never through each other's modules.

This is asserted by parsing the imports rather than by trusting a linter,
because the failure is architectural and invisible at runtime: nothing stops
Python from importing whatever it likes, and a cross-context import compiles,
lints and passes every test. It only shows up later as a change in one context
silently breaking another.

`src.interfaces.web.app` is exempt -- it is the composition root, and wiring
the contexts together is its one job.
"""

import ast
from pathlib import Path

import pytest

SRC = Path("src")

#: Every bounded context directory under `src/`.
CONTEXTS = sorted(
    path.name
    for path in SRC.iterdir()
    if path.is_dir() and (path / "__init__.py").exists() and path.name != "shared"
)

#: The shared kernel and the composition root are meant to be imported by
#: everyone, so an import *of* them is never a violation.
EXEMPT_IMPORTERS = {"shared", "interfaces"}

#: Importing the shared kernel is always allowed.
ALWAYS_ALLOWED = "src.shared"


def imported_modules(path: Path) -> set[str]:
    """Return the absolute module names imported by one file."""
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def owning_context(path: Path) -> str | None:
    """The bounded context a file belongs to, or None if it is outside them."""
    relative = path.relative_to(SRC)
    return relative.parts[0] if relative.parts[0] in CONTEXTS else None


@pytest.mark.parametrize("context", CONTEXTS)
def test_the_expected_contexts_exist(context):
    """Guards against CONTEXTS silently becoming empty, which would pass all."""
    assert (SRC / context / "__init__.py").exists()


@pytest.mark.parametrize(
    "path",
    sorted(p for p in SRC.rglob("*.py") if owning_context(p) not in EXEMPT_IMPORTERS),
    ids=lambda p: str(p),
)
def test_context_does_not_import_another_context(path):
    importer = owning_context(path)
    if importer is None:  # the shared kernel itself
        pytest.skip("the shared kernel is below every context")

    violations = []
    for module in imported_modules(path):
        if module.startswith(ALWAYS_ALLOWED):
            continue
        parts = module.split(".")
        if len(parts) < 2 or parts[0] != "src":
            continue
        target = parts[1]
        if target in CONTEXTS and target != importer:
            violations.append(module)

    assert not violations, (
        f"{path} belongs to '{importer}' but imports {sorted(violations)} "
        f"from another bounded context. Either the import belongs in "
        f"src/shared, or the two contexts need a shared vocabulary."
    )
