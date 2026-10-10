"""cache-cli: exercise the payload service from the command line.

Usage:
    cache-cli [-h|--host URL] [-r|--repeat N] [-i|--input FILE|-]
              [-j|--json JSON] [-o|--output FILE|-] [--help]
"""

import argparse
import json
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Self, TextIO

import httpx
from pydantic import AliasChoices, AnyHttpUrl, Field, PositiveInt, ValidationError, model_validator
from pydantic_settings import (
    BaseSettings,
    CliSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from payload_cache.schemas import PayloadCreate

STDIO = "-"
EXIT_OK = 0
EXIT_REQUEST_FAILED = 1
EXIT_USAGE = 2
# Generous on purpose: a request full of uncached strings waits on the transformer.
REQUEST_TIMEOUT_SECONDS = 30.0


class CliSettings(BaseSettings):
    """Command line arguments, parsed and validated by Pydantic Settings."""

    model_config = SettingsConfigDict(cli_hide_none_type=True)

    host: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://localhost:8000"),
        validation_alias=AliasChoices("host", "h"),
        description="Base URL of the server",
    )
    repeat: PositiveInt = Field(
        default=1,
        validation_alias=AliasChoices("repeat", "r"),
        description="Number of times to send the request",
    )
    input: str | None = Field(
        default=None,
        validation_alias=AliasChoices("input", "i"),
        description='JSON file holding the request body ("-" for stdin)',
    )
    json_text: str | None = Field(
        default=None,
        validation_alias=AliasChoices("json", "j"),
        description="Request body as an inline JSON string",
    )
    output: str = Field(
        default=STDIO,
        validation_alias=AliasChoices("output", "o"),
        description='File to write results to ("-" for stdout)',
    )

    @model_validator(mode="after")
    def exactly_one_input_source(self) -> Self:
        if (self.input is None) == (self.json_text is None):
            raise ValueError("provide exactly one of --input or --json")
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Arguments only. Left enabled, the environment source would silently
        # pick up unrelated variables that happen to be named HOST or OUTPUT.
        return (init_settings,)


def parse_args(argv: Sequence[str]) -> CliSettings:
    # argparse claims -h for help by default, but the tool's interface gives
    # -h to --host, so help is registered under its long name only.
    parser = argparse.ArgumentParser(
        prog="cache-cli",
        description="Create a payload on the server and read it back.",
        add_help=False,
    )
    parser.add_argument("--help", action="help", help="show this help message and exit")
    source = CliSettingsSource(CliSettings, root_parser=parser)
    return CliSettings(_cli_settings_source=source(args=list(argv)))


def load_request(settings: CliSettings) -> PayloadCreate:
    """Read the request body from --json, a file, or stdin, and validate it.

    Validating with the server's own schema reports a malformed body locally,
    without spending a round trip to be told the same thing.
    """
    if settings.json_text is not None:
        text = settings.json_text
    elif settings.input == STDIO:
        text = sys.stdin.read()
    else:
        text = Path(settings.input).read_text(encoding="utf-8")
    return PayloadCreate.model_validate_json(text)


@contextmanager
def open_output(target: str) -> Iterator[TextIO]:
    if target == STDIO:
        yield sys.stdout
    else:
        with Path(target).open("w", encoding="utf-8") as file:
            yield file


def run(client: httpx.Client, request: PayloadCreate, repeat: int, out: TextIO) -> None:
    """Create and read back the payload `repeat` times, one JSON line per iteration.

    Repeating the same request is what makes the cache observable: after the
    first iteration `created` turns false while `id` stays the same.
    """
    body = request.model_dump()
    for iteration in range(1, repeat + 1):
        started = time.perf_counter()
        created = client.post("/payload", json=body)
        created.raise_for_status()
        payload_id = created.json()["id"]
        read = client.get(f"/payload/{payload_id}")
        read.raise_for_status()
        elapsed_ms = (time.perf_counter() - started) * 1000

        record = {
            "iteration": iteration,
            "id": payload_id,
            "created": created.status_code == httpx.codes.CREATED,
            "output": read.json()["output"],
            "elapsed_ms": round(elapsed_ms, 1),
        }
        out.write(json.dumps(record) + "\n")


def _describe(error: ValidationError) -> str:
    """Condense a validation error to one line fit for a terminal."""
    problems = []
    for detail in error.errors():
        location = ".".join(str(part) for part in detail["loc"])
        problems.append(f"{location}: {detail['msg']}" if location else detail["msg"])
    return "; ".join(problems)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        settings = parse_args(sys.argv[1:] if argv is None else argv)
        request = load_request(settings)
    except ValidationError as error:
        print(f"cache-cli: invalid input: {_describe(error)}", file=sys.stderr)
        return EXIT_USAGE
    except OSError as error:
        print(f"cache-cli: cannot read input: {error}", file=sys.stderr)
        return EXIT_USAGE

    try:
        with (
            httpx.Client(base_url=str(settings.host), timeout=REQUEST_TIMEOUT_SECONDS) as client,
            open_output(settings.output) as out,
        ):
            run(client, request, settings.repeat, out)
    except httpx.HTTPError as error:
        print(f"cache-cli: request to {settings.host} failed: {error}", file=sys.stderr)
        return EXIT_REQUEST_FAILED
    except OSError as error:
        print(f"cache-cli: cannot write output: {error}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
