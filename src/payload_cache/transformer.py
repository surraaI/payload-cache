"""The "transformer function" and the guard that keeps calls to it minimal.

The real transformer is an external service, so it is modelled as an async
callable: slow, and worth never calling twice for the same input.
"""

import asyncio
from collections.abc import Awaitable, Callable

Transformer = Callable[[str], Awaitable[str]]


def make_uppercase_transformer(delay_seconds: float) -> Transformer:
    """Build the stand-in transformer: uppercases after a simulated network delay."""

    async def transform(text: str) -> str:
        await asyncio.sleep(delay_seconds)
        return text.upper()

    return transform


class CoalescingTransformer:
    """Shares one in-flight transformer call between concurrent callers.

    The database cache only helps once a result has been stored. Without this,
    two requests arriving together with the same new string would both miss the
    cache and both call the transformer.
    """

    def __init__(self, transform: Transformer) -> None:
        self._transform = transform
        self._in_flight: dict[str, asyncio.Task[str]] = {}

    async def __call__(self, text: str) -> str:
        task = self._in_flight.get(text)
        if task is None:
            task = asyncio.create_task(self._transform(text))
            self._in_flight[text] = task
            task.add_done_callback(lambda _: self._in_flight.pop(text, None))
        # Shielded so that one caller being cancelled (e.g. a client disconnect)
        # does not cancel the call for everyone else waiting on it.
        return await asyncio.shield(task)
