"""Automatic access-token refresh and single retry on 401 responses."""

import functools
import inspect
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from src.shared.http.drf_client import UnauthorizedError

T = TypeVar("T")

#: Returns a fresh ``(access_token, refresh_token)`` pair.
RefreshCallable = Callable[[], Awaitable[tuple[str, str]]]
#: Called with the new token pair so the caller can persist it (e.g. rewrite the
#: response cookies). May be sync or async.
TokensRefreshedCallback = Callable[[str, str], Any]

#: Methods that must never trigger the refresh flow: refreshing in order to
#: refresh would recurse forever.
NO_REFRESH_METHODS = frozenset({"refresh_token"})


class AutoRefreshingRepository:
    """Transparent proxy that refreshes the access token once on ``401``.

    Wraps any repository port implementation without changing its public
    surface: every coroutine method whose first positional argument is the
    access token is retried exactly once with a freshly minted token after the
    wrapped call raises :class:`UnauthorizedError`. The new token pair is passed
    to ``on_refreshed`` before the retry, so the caller can persist it.

    The retried call is not itself guarded, so a second ``401`` propagates to
    the caller instead of looping.

    Example::

        repo = AutoRefreshingRepository(
            inner=DRFTransactionRepository(api_client),
            refresh=lambda: auth_repository.refresh_token(refresh_cookie),
            on_refreshed=lambda a, r: cookie_manager.set_tokens(response, a, r),
        )
    """

    def __init__(
        self,
        inner: T,
        refresh: RefreshCallable,
        on_refreshed: TokensRefreshedCallback | None = None,
    ) -> None:
        self._inner = inner
        self._refresh = refresh
        self._on_refreshed = on_refreshed
        self.refresh_count = 0

    def __getattr__(self, name: str) -> Any:
        # Guard against recursion during unpickling/copying, before __init__ ran.
        if name.startswith("_"):
            raise AttributeError(name)

        attribute = getattr(self._inner, name)
        if name in NO_REFRESH_METHODS or not inspect.iscoroutinefunction(attribute):
            return attribute
        return functools.wraps(attribute)(self._retrying(attribute))

    def _retrying(self, method: Callable[..., Awaitable[Any]]) -> Callable[..., Any]:
        @functools.wraps(method)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await method(*args, **kwargs)
            except UnauthorizedError:
                if not args:
                    # No access token was supplied; there is nothing to refresh.
                    raise
                new_access, _new_refresh = await self._rotate()
                return await method(new_access, *args[1:], **kwargs)

        return wrapper

    async def _rotate(self) -> tuple[str, str]:
        tokens = await self._refresh()
        self.refresh_count += 1
        if self._on_refreshed is not None:
            outcome = self._on_refreshed(*tokens)
            if inspect.isawaitable(outcome):
                await outcome
        return tokens

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._inner!r})"
