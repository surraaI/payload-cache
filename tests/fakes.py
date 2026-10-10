"""Test doubles shared across the test suite."""

import asyncio


class CountingTransformer:
    """Uppercases like the real stand-in, recording every call it receives.

    The call log is what lets tests assert the service's central promise:
    the transformer is never called for a string it has already seen.
    """

    def __init__(self, delay_seconds: float = 0.0) -> None:
        self.calls: list[str] = []
        self._delay_seconds = delay_seconds

    async def __call__(self, text: str) -> str:
        self.calls.append(text)
        await asyncio.sleep(self._delay_seconds)
        return text.upper()
