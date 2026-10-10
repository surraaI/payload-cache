"""Request and response bodies of the HTTP API."""

from typing import Annotated, Self

from pydantic import BaseModel, Field, StringConstraints, model_validator

# Every distinct string in a request can cost one transformer call, so the
# request size is bounded to keep a single client from flooding the service.
MAX_ITEMS_PER_LIST = 1000
MAX_STRING_LENGTH = 1000

Item = Annotated[str, StringConstraints(max_length=MAX_STRING_LENGTH)]
ItemList = Annotated[list[Item], Field(min_length=1, max_length=MAX_ITEMS_PER_LIST)]


class PayloadCreate(BaseModel):
    list_1: ItemList
    list_2: ItemList

    @model_validator(mode="after")
    def lists_have_equal_length(self) -> Self:
        if len(self.list_1) != len(self.list_2):
            raise ValueError("list_1 and list_2 must have the same length")
        return self


class PayloadCreated(BaseModel):
    id: str
    message: str


class PayloadRead(BaseModel):
    output: str
