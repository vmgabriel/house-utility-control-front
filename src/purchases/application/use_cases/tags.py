"""Use cases for the shared tag catalogue.

The catalogue is global: `Tag` has no owner column and `TagListView` takes no
user, so a tag created by one person is available to every other. That is the
backend's design and it is why this context never labels the list "your tags".

Only one use case is needed for what the BFF renders. Nothing in the UI creates
a tag on its own: tags arrive *with* an item, because the item-tag endpoints take
``tag_names`` and create whatever they are given
(``POST .../items/<id>/tags/`` -> ``AddTagsSerializer``). So the catalogue is read
for suggestions and written only as a side effect of tagging an item.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.purchases.domain.entities import Tag
from src.purchases.domain.ports import TagRepositoryPort


@dataclass(frozen=True, slots=True)
class ListTagsUseCase:
    """List the shared tag catalogue, for the tag suggestion input.

    Thin, and the backend owns the ordering and the set. A failure here is
    caught by the caller as an optional panel: a broken autocomplete must not
    take the page down, so the view treats this read as degradable rather than
    required.
    """

    tags: TagRepositoryPort

    async def execute(self, access_token: str) -> list[Tag]:
        """Return every tag in the catalogue, or an empty list."""
        return await self.tags.get_all_tags(access_token)


__all__ = ["ListTagsUseCase"]
