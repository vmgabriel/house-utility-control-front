"""Report whether the LLM provider behind the backend is reachable."""

from dataclasses import dataclass

from src.shared.domain.exceptions import DomainException
from src.tasks.application.ports import TasksApiClient
from src.tasks.domain.value_objects import LlmHealth


@dataclass(frozen=True, slots=True)
class GetLlmHealthUseCase:
    """Return the health state for the navbar indicator.

    The one use case here that swallows an exception, and it does so on purpose.
    The indicator polls this from every page, so a backend outage would
    otherwise put a red error banner above whatever the user was trying to do,
    for a decoration that is not the subject of the page. Returning
    `LlmHealth.indeterminate` instead keeps the dot amber and the rest of the
    page working, which is the correct degradation: an unavailable LLM costs
    the user the decompose buttons and nothing else.

    `UnauthorizedError` is caught with everything else, because a token that
    could not be refreshed is also a fact about the LLM being unknowable. The
    session-expiry path still runs: the page's other use cases hit the same
    wall and redirect. Any deliberate `DomainException` qualifies -- the health
    probe has no opinion about *why* it could not answer, only that it did not.
    """

    tasks_client: TasksApiClient

    async def execute(self, access_token: str) -> LlmHealth:
        try:
            return await self.tasks_client.get_llm_health(access_token)
        except DomainException as exc:
            return LlmHealth.indeterminate(str(exc))
