"""Purchases bounded context: shopping lists and the wishlist.

Isolated on purpose. `src/purchases` talks to the DRF purchases API and to the
shared kernel, and to nothing else in `src/` -- enforced by
`tests/integration/test_context_boundaries.py`.
"""
