"""Integration tests: HTTP request -> service -> SQLite and back."""

import asyncio
from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fakes import CountingTransformer
from payload_cache.config import Settings
from payload_cache.main import create_app
from payload_cache.schemas import MAX_ITEMS_PER_LIST

SAMPLE_REQUEST = {
    "list_1": ["first string", "second string", "third string"],
    "list_2": ["other string", "another string", "last string"],
}
SAMPLE_OUTPUT = (
    "FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING, THIRD STRING, LAST STRING"
)


@pytest.fixture
def app(database_url, transformer) -> FastAPI:
    return create_app(Settings(database_url=database_url), transformer)


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    # Entering the context runs the app's startup and shutdown.
    with TestClient(app) as client:
        yield client


def test_create_then_read_returns_the_sample_output(client):
    created = client.post("/payload", json=SAMPLE_REQUEST)

    assert created.status_code == 201
    assert created.json()["message"] == "Payload created"

    read = client.get(f"/payload/{created.json()['id']}")

    assert read.status_code == 200
    assert read.json() == {"output": SAMPLE_OUTPUT}


def test_repeated_create_reuses_the_id_and_skips_the_transformer(client, transformer):
    first = client.post("/payload", json=SAMPLE_REQUEST)
    calls_after_first = len(transformer.calls)

    second = client.post("/payload", json=SAMPLE_REQUEST)

    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert len(transformer.calls) == calls_after_first == 6


def test_overlapping_request_only_transforms_new_strings(client, transformer):
    client.post("/payload", json=SAMPLE_REQUEST)
    transformer.calls.clear()

    client.post("/payload", json={"list_1": ["first string"], "list_2": ["brand new"]})

    assert transformer.calls == ["brand new"]


def test_cache_survives_a_restart(database_url, transformer):
    with TestClient(create_app(Settings(database_url=database_url), transformer)) as client:
        first_id = client.post("/payload", json=SAMPLE_REQUEST).json()["id"]

    restarted_transformer = CountingTransformer()
    restarted_app = create_app(Settings(database_url=database_url), restarted_transformer)
    with TestClient(restarted_app) as client:
        response = client.post("/payload", json=SAMPLE_REQUEST)

        assert response.json()["id"] == first_id
        assert restarted_transformer.calls == []
        assert client.get(f"/payload/{first_id}").json() == {"output": SAMPLE_OUTPUT}


def test_read_unknown_id_returns_404(client):
    response = client.get("/payload/does-not-exist")

    assert response.status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"list_1": ["a", "b"], "list_2": ["c"]}, id="different lengths"),
        pytest.param({"list_1": [], "list_2": []}, id="empty lists"),
        pytest.param({"list_1": ["a"]}, id="missing list"),
        pytest.param({"list_1": [1], "list_2": ["a"]}, id="non-string item"),
        pytest.param(
            {
                "list_1": ["a"] * (MAX_ITEMS_PER_LIST + 1),
                "list_2": ["b"] * (MAX_ITEMS_PER_LIST + 1),
            },
            id="too many items",
        ),
    ],
)
def test_invalid_request_is_rejected_before_any_transformer_call(client, transformer, body):
    response = client.post("/payload", json=body)

    assert response.status_code == 422
    assert transformer.calls == []


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


async def test_concurrent_identical_requests_transform_each_string_once(database_url):
    # A real delay keeps the first request's transformer calls in flight
    # while the second request arrives.
    transformer = CountingTransformer(delay_seconds=0.05)
    app = create_app(Settings(database_url=database_url), transformer)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        responses = await asyncio.gather(
            client.post("/payload", json=SAMPLE_REQUEST),
            client.post("/payload", json=SAMPLE_REQUEST),
        )

    assert sorted(transformer.calls) == sorted(SAMPLE_REQUEST["list_1"] + SAMPLE_REQUEST["list_2"])
    assert responses[0].json()["id"] == responses[1].json()["id"]
    assert sorted(response.status_code for response in responses) == [200, 201]
