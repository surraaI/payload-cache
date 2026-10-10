import pytest

from payload_cache.service import PayloadService, interleave, payload_id_for

LIST_1 = ["first string", "second string", "third string"]
LIST_2 = ["other string", "another string", "last string"]
EXPECTED_OUTPUT = (
    "FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING, THIRD STRING, LAST STRING"
)


@pytest.fixture
def service(session, transformer) -> PayloadService:
    return PayloadService(session, transformer)


def test_interleave_alternates_starting_with_the_first_list():
    assert interleave(["a", "b"], ["1", "2"]) == ["a", "1", "b", "2"]


def test_interleave_rejects_lists_of_different_length():
    with pytest.raises(ValueError):
        interleave(["a", "b"], ["1"])


def test_payload_id_is_stable_and_content_dependent():
    assert payload_id_for("A, B") == payload_id_for("A, B")
    assert payload_id_for("A, B") != payload_id_for("B, A")


async def test_create_generates_the_interleaved_transformed_output(service):
    payload_id, created = await service.create(LIST_1, LIST_2)

    assert created is True
    assert await service.get_output(payload_id) == EXPECTED_OUTPUT


async def test_repeated_request_reuses_id_without_calling_the_transformer(service, transformer):
    first_id, _ = await service.create(LIST_1, LIST_2)
    calls_after_first = len(transformer.calls)

    second_id, created = await service.create(LIST_1, LIST_2)

    assert second_id == first_id
    assert created is False
    assert len(transformer.calls) == calls_after_first


async def test_only_unseen_strings_reach_the_transformer(service, transformer):
    await service.create(["a", "b"], ["c", "d"])
    transformer.calls.clear()

    await service.create(["a", "new"], ["d", "c"])

    assert transformer.calls == ["new"]


async def test_string_repeated_within_a_request_is_transformed_once(service, transformer):
    payload_id, _ = await service.create(["a", "a"], ["a", "b"])

    assert sorted(transformer.calls) == ["a", "b"]
    assert await service.get_output(payload_id) == "A, A, A, B"


async def test_different_inputs_with_identical_output_share_an_id(service):
    """The id identifies the payload itself, not the request that produced it."""
    lower_id, _ = await service.create(["a"], ["b"])
    upper_id, created = await service.create(["A"], ["B"])

    assert upper_id == lower_id
    assert created is False


async def test_get_output_returns_none_for_unknown_id(service):
    assert await service.get_output("missing") is None
