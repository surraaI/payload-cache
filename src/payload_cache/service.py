"""Payload generation: transform, interleave, store."""

import asyncio
import hashlib
from collections.abc import Sequence

from sqlalchemy.dialects.sqlite import insert
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from payload_cache.models import Payload, TransformedString
from payload_cache.transformer import Transformer

SEPARATOR = ", "


def interleave(first: Sequence[str], second: Sequence[str]) -> list[str]:
    """Alternate the items of two equally long sequences, starting with the first."""
    return [item for pair in zip(first, second, strict=True) for item in pair]


def payload_id_for(output: str) -> str:
    """Derive a payload's identifier from its content."""
    return hashlib.sha256(output.encode()).hexdigest()


class PayloadService:
    def __init__(self, session: AsyncSession, transform: Transformer) -> None:
        self._session = session
        self._transform = transform

    async def create(self, list_1: Sequence[str], list_2: Sequence[str]) -> tuple[str, bool]:
        """Generate the payload for two lists.

        Returns the payload id and whether this call created it (False means
        an identical payload already existed and its id was reused).
        """
        transformed = await self._transform_all({*list_1, *list_2})
        output = SEPARATOR.join(transformed[item] for item in interleave(list_1, list_2))
        payload_id = payload_id_for(output)

        # Insert-or-ignore instead of select-then-insert: two concurrent
        # requests for the same payload cannot both pass a "does it exist?"
        # check and then collide on the primary key.
        result = await self._session.exec(
            insert(Payload).values(id=payload_id, output=output).on_conflict_do_nothing()
        )
        await self._session.commit()
        return payload_id, result.rowcount == 1

    async def get_output(self, payload_id: str) -> str | None:
        payload = await self._session.get(Payload, payload_id)
        return payload.output if payload else None

    async def _transform_all(self, strings: set[str]) -> dict[str, str]:
        """Map each string to its transformed form, calling the transformer only for misses.

        Taking a set is deliberate: a string repeated within one request is
        transformed once.
        """
        rows = await self._session.exec(
            select(TransformedString).where(col(TransformedString.source).in_(strings))
        )
        transformed = {row.source: row.result for row in rows}

        missing = sorted(strings - transformed.keys())
        if not missing:
            return transformed

        results = await asyncio.gather(*(self._transform(text) for text in missing))
        fresh = dict(zip(missing, results, strict=True))

        await self._session.exec(
            insert(TransformedString)
            .values([{"source": source, "result": result} for source, result in fresh.items()])
            .on_conflict_do_nothing()
        )
        # Committed on its own: these results were paid for with transformer
        # calls and should outlive any failure later in the request.
        await self._session.commit()

        return transformed | fresh
