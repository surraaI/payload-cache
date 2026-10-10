import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from payload_cache import cli
from payload_cache.config import Settings
from payload_cache.main import create_app
from payload_cache.schemas import PayloadCreate

BODY = {"list_1": ["first string", "second string"], "list_2": ["other string", "another string"]}
BODY_JSON = json.dumps(BODY)
EXPECTED_OUTPUT = "FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING"


@pytest.fixture
def server(database_url, transformer, monkeypatch):
    """Route the CLI's HTTP client into an in-process app instead of the network."""
    app = create_app(Settings(database_url=database_url), transformer)
    with TestClient(app) as client:
        # TestClient is an httpx.Client, so the CLI can use it unchanged.
        monkeypatch.setattr(cli.httpx, "Client", lambda **_: client)
        yield client


# --- argument parsing -------------------------------------------------------


def test_defaults():
    settings = cli.parse_args(["--json", BODY_JSON])

    assert str(settings.host) == "http://localhost:8000/"
    assert settings.repeat == 1
    assert settings.output == "-"


def test_long_flags():
    settings = cli.parse_args(
        [
            "--host",
            "http://example.com:9000",
            "--repeat",
            "3",
            "--input",
            "in.json",
            "--output",
            "o",
        ]
    )

    assert str(settings.host) == "http://example.com:9000/"
    assert (settings.repeat, settings.input, settings.output) == (3, "in.json", "o")


def test_short_flags_including_h_for_host():
    settings = cli.parse_args(["-h", "http://example.com", "-r", "2", "-j", BODY_JSON, "-o", "o"])

    assert str(settings.host) == "http://example.com/"
    assert (settings.repeat, settings.json_text, settings.output) == (2, BODY_JSON, "o")


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param([], id="no input source"),
        pytest.param(["--input", "f.json", "--json", BODY_JSON], id="both input sources"),
        pytest.param(["--json", BODY_JSON, "--repeat", "0"], id="repeat below one"),
        pytest.param(["--json", BODY_JSON, "--repeat", "many"], id="repeat not a number"),
        pytest.param(["--json", BODY_JSON, "--host", "not-a-url"], id="host not a url"),
        pytest.param(["--json", BODY_JSON, "--host", "ftp://example.com"], id="host not http"),
    ],
)
def test_invalid_arguments_are_rejected(argv):
    with pytest.raises(ValidationError):
        cli.parse_args(argv)


def test_environment_variables_do_not_leak_into_settings(monkeypatch):
    monkeypatch.setenv("HOST", "http://from-environment")

    settings = cli.parse_args(["--json", BODY_JSON])

    assert str(settings.host) == "http://localhost:8000/"


def test_help_is_long_form_only(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.parse_args(["--help"])

    assert exit_info.value.code == 0
    assert "--host" in capsys.readouterr().out


# --- reading the request body -----------------------------------------------


def test_load_request_from_inline_json():
    request = cli.load_request(cli.parse_args(["--json", BODY_JSON]))

    assert request == PayloadCreate(**BODY)


def test_load_request_from_file(tmp_path):
    path = tmp_path / "request.json"
    path.write_text(BODY_JSON, encoding="utf-8")

    request = cli.load_request(cli.parse_args(["--input", str(path)]))

    assert request == PayloadCreate(**BODY)


def test_load_request_from_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(BODY_JSON))

    request = cli.load_request(cli.parse_args(["--input", "-"]))

    assert request == PayloadCreate(**BODY)


# --- end to end against the API ---------------------------------------------


def test_run_shows_the_cache_taking_effect_across_iterations(server, transformer):
    out = io.StringIO()

    cli.run(server, PayloadCreate(**BODY), repeat=3, out=out)

    records = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [record["iteration"] for record in records] == [1, 2, 3]
    assert [record["created"] for record in records] == [True, False, False]
    assert len({record["id"] for record in records}) == 1
    assert all(record["output"] == EXPECTED_OUTPUT for record in records)
    assert len(transformer.calls) == 4


def test_main_writes_results_to_stdout(server, capsys):
    exit_code = cli.main(["--json", BODY_JSON])

    assert exit_code == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["output"] == EXPECTED_OUTPUT


def test_main_reads_a_file_and_writes_a_file(server, tmp_path):
    request_path = tmp_path / "request.json"
    request_path.write_text(BODY_JSON, encoding="utf-8")
    result_path = tmp_path / "result.jsonl"

    exit_code = cli.main(["-i", str(request_path), "-o", str(result_path), "-r", "2"])

    assert exit_code == cli.EXIT_OK
    lines = result_path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["created"] for line in lines] == [True, False]


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["--json", "{not json"], id="malformed json"),
        pytest.param(["--json", '{"list_1": ["a"], "list_2": []}'], id="unequal lists"),
        pytest.param(["--input", "missing-file.json"], id="missing input file"),
        pytest.param(["--json", BODY_JSON, "--repeat", "0"], id="bad argument"),
    ],
)
def test_main_reports_bad_input_without_contacting_the_server(server, transformer, capsys, argv):
    exit_code = cli.main(argv)

    assert exit_code == cli.EXIT_USAGE
    assert capsys.readouterr().err.startswith("cache-cli: ")
    assert transformer.calls == []


def test_main_reports_an_unreachable_server(monkeypatch, capsys):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    real_client = httpx.Client
    monkeypatch.setattr(
        cli.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(refuse), **kwargs),
    )

    exit_code = cli.main(["--json", BODY_JSON])

    assert exit_code == cli.EXIT_REQUEST_FAILED
    assert "connection refused" in capsys.readouterr().err
