"""Database tables."""

from sqlmodel import Field, SQLModel


class TransformedString(SQLModel, table=True):
    """One cached transformer result.

    Cached per string rather than per request, so a string shared between two
    different requests is still only ever sent to the transformer once.
    """

    __tablename__ = "transformed_string"

    source: str = Field(primary_key=True)
    result: str


class Payload(SQLModel, table=True):
    """A generated payload, addressed by a hash of its own content.

    Deriving the id from the output is what makes identifiers reusable: the
    same payload always maps to the same row, with no lookup-then-insert race.
    """

    id: str = Field(primary_key=True)
    output: str
