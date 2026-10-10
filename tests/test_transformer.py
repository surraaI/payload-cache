import asyncio

import pytest

from fakes import CountingTransformer
from payload_cache.transformer import CoalescingTransformer, make_uppercase_transformer


async def test_uppercase_transformer_uppercases():
    transform = make_uppercase_transformer(delay_seconds=0)

    assert await transform("first string") == "FIRST STRING"


async def test_concurrent_calls_for_the_same_string_share_one_call():
    inner = CountingTransformer(delay_seconds=0.01)
    transform = CoalescingTransformer(inner)

    results = await asyncio.gather(*(transform("same") for _ in range(5)))

    assert results == ["SAME"] * 5
    assert inner.calls == ["same"]


async def test_different_strings_are_not_coalesced():
    inner = CountingTransformer(delay_seconds=0.01)
    transform = CoalescingTransformer(inner)

    results = await asyncio.gather(transform("a"), transform("b"))

    assert results == ["A", "B"]
    assert sorted(inner.calls) == ["a", "b"]


async def test_failed_call_is_not_remembered():
    """A transient failure must not poison later requests for the same string."""
    attempts = 0

    async def flaky(text: str) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("service unavailable")
        return text.upper()

    transform = CoalescingTransformer(flaky)

    with pytest.raises(RuntimeError):
        await transform("a")
    assert await transform("a") == "A"


async def test_cancelled_caller_does_not_cancel_the_shared_call():
    inner = CountingTransformer(delay_seconds=0.05)
    transform = CoalescingTransformer(inner)

    first = asyncio.create_task(transform("a"))
    second = asyncio.create_task(transform("a"))
    await asyncio.sleep(0)
    first.cancel()

    assert await second == "A"
    assert inner.calls == ["a"]
