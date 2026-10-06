"""Exceptions the identity context speaks in.

`AuthenticationError` is declared in the shared kernel because
`src.shared.http.drf_client` raises it on a rejected login, and the shared
kernel sits below every context. Re-exported here so identity code reads its
vocabulary from its own package rather than reaching into `src.shared`.
"""

from src.shared.domain.exceptions import AuthenticationError, DomainException

__all__ = ["AuthenticationError", "DomainException"]
