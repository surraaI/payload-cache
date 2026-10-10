# payload-cache

A FastAPI microservice that generates payloads from two lists of strings and
caches the expensive part of the work in SQLite.

Each string is passed through a "transformer function" (a stand-in for a slow
external service), and the transformed strings of the two lists are interleaved
into one payload. The service calls the transformer as rarely as it can: every
result is stored, and a string is never transformed twice.

```
POST /payload
{"list_1": ["first string", "second string", "third string"],
 "list_2": ["other string", "another string", "last string"]}

-> 201 {"id": "c41c0f74...", "message": "Payload created"}

GET /payload/c41c0f74...

-> 200 {"output": "FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING, THIRD STRING, LAST STRING"}
```

## Run it

### With Docker

```sh
docker compose up -d --build --wait
```

The API is then on http://localhost:8000, with interactive docs at `/docs`.
`--wait` returns once the health check passes, so the service is ready for the
next command. The SQLite file lives on a named volume and survives
`docker compose down`; add `-v` to delete it and start with an empty cache.

The image also contains the CLI:

```sh
docker compose exec api cache-cli --repeat 3 \
  --json '{"list_1": ["first string"], "list_2": ["other string"]}'
```

### Locally

Requires Python 3.11 or newer.

```sh
python -m venv .venv
.venv/bin/pip install -e ".[dev]"        # Windows: .venv\Scripts\pip
.venv/bin/uvicorn payload_cache.main:create_app --factory
```

### Configuration

| Environment variable | Default | Meaning |
|---|---|---|
| `PAYLOAD_CACHE_DATABASE_URL` | `sqlite+aiosqlite:///./payload_cache.db` | Where the cache is stored |
| `PAYLOAD_CACHE_TRANSFORMER_DELAY_SECONDS` | `0.1` | Simulated latency of the stand-in transformer |

## API

| Endpoint | Response |
|---|---|
| `POST /payload` | `201` with `id` and `message` when a new payload is stored; `200` with the same shape when an existing identifier is reused; `422` for invalid input |
| `GET /payload/{id}` | `200` with `output`; `404` for an unknown id |
| `GET /health` | `200`, used by the container health check |

A request must contain two non-empty lists of equal length, with at most 1000
strings per list and 1000 characters per string. The limits exist because every
distinct string can cost one transformer call.

## CLI

```
cache-cli [-h|--host URL] [-r|--repeat N] [-i|--input FILE|-] [-j|--json JSON] [-o|--output FILE|-] [--help]
```

| Flag | Default | Meaning |
|---|---|---|
| `-h`, `--host` | `http://localhost:8000` | Base URL of the server |
| `-r`, `--repeat` | `1` | Number of times to send the request |
| `-i`, `--input` | | JSON file with the request body, `-` for stdin |
| `-j`, `--json` | | Request body as an inline JSON string |
| `-o`, `--output` | `-` | File for the results, `-` for stdout |

Exactly one of `--input` and `--json` is required. Arguments are parsed and
validated with Pydantic Settings, and the request body is checked locally
against the server's own schema before anything is sent.

Each iteration creates the payload, reads it back, and writes one JSON line:

```json
{"iteration": 1, "id": "ef0b8724...", "created": true, "output": "FIRST STRING, OTHER STRING", "elapsed_ms": 151.9}
{"iteration": 2, "id": "ef0b8724...", "created": false, "output": "FIRST STRING, OTHER STRING", "elapsed_ms": 5.6}
```

With `--repeat` above 1 the cache becomes visible: `created` turns false, the
id stays the same, and the elapsed time drops.

Exit codes: `0` success, `1` the request failed, `2` invalid arguments or input.

`python -m payload_cache.cli` is equivalent to `cache-cli`, for systems where
the generated launcher cannot be run.

## Tests

```sh
.venv/bin/python -m pytest
.venv/bin/ruff check .
```

- **Unit tests** cover the transformer and call coalescing
  (`tests/test_transformer.py`), the service logic (`tests/test_service.py`),
  and CLI argument parsing (`tests/test_cli.py`).
- **Integration tests** send HTTP requests through the whole stack to a
  temporary SQLite file (`tests/test_api.py`), and drive the CLI against that
  same in-process app (`tests/test_cli.py`).

Tests replace the transformer with a fake that records its calls
(`tests/fakes.py`). That record is how the tests assert the central promise:
repeated or overlapping requests cause no further transformer calls, including
after a restart and under concurrent identical requests.

## How a request flows

`POST /payload` with the sample above:

1. **Validation** (`schemas.py`). Pydantic rejects unequal, empty or oversized
   lists with `422` before any work is done.
2. **Wiring** (`api.py`). The endpoint receives a `PayloadService` holding a
   database session for this request and the app-wide transformer.
3. **Cache lookup** (`service.py`). The distinct strings of both lists are
   looked up in the `transformed_string` table with one query.
4. **Transform the misses** (`transformer.py`). Only strings not found are sent
   to the transformer, concurrently. Their results are inserted into
   `transformed_string` and committed.
5. **Build the payload** (`service.py`). The lists are interleaved, each string
   is replaced by its transformed form, and the result is joined with `", "`.
6. **Store it** (`service.py`). The payload id is the SHA-256 of the output.
   The row is inserted into `payload` unless it already exists.
7. **Respond** (`api.py`). `201` if the row was inserted, `200` if it was
   already there.

`GET /payload/{id}` is a primary-key read of the `payload` table.

| Table | Columns | Role |
|---|---|---|
| `transformed_string` | `source` (PK), `result` | Transformer cache |
| `payload` | `id` (PK), `output` | Generated payloads |

## Assumptions

These were raised with the task author before starting:

- **`-h` is `--host`.** The task lists `-h` for both `--host` and `--help`.
  `-h` goes to `--host`, and help is available as `--help` only.
- **The transformer is a stand-in.** It uppercases the string after a short
  delay to simulate an external service. `create_app()` accepts any other
  async `str -> str` callable in its place.
- **The cache is per string, not per pair of lists.** A string shared by two
  different requests is transformed once.

## Design decisions

- **SQLite.** The task allows SQLite or PostgreSQL. SQLite needs no second
  container, so the service runs with one command, and the file on a volume
  gives the durability the cache needs. All access goes through
  SQLModel/SQLAlchemy; moving to PostgreSQL means a different driver, the
  PostgreSQL variant of the `insert ... on conflict` statement in `service.py`,
  and migrations.
- **Payload ids are content hashes.** The id is derived from the output, so
  "reuse the identifier of a payload generated before" needs no lookup, and two
  different inputs that generate the same payload share one id.
- **Insert-or-ignore writes.** Both tables are written with
  `ON CONFLICT DO NOTHING` rather than select-then-insert, so concurrent
  identical requests cannot collide on a primary key.
- **In-flight calls are shared.** The database cache only helps once a result
  is stored. `CoalescingTransformer` lets concurrent requests for the same new
  string await one transformer call instead of each making their own.
- **Transformer results are committed before the payload is built.** They cost
  transformer calls and should survive a failure later in the request.
- **Async throughout.** The transformer models network I/O, so requests waiting
  on it should not block each other.

## Shortcuts and limitations

- **No migrations.** Tables are created at startup with `create_all`. With two
  tables and no schema history this is enough; a schema change would call for
  Alembic.
- **Call sharing is per process.** With several workers or replicas, two of
  them can each transform the same new string once. The result is still
  correct (the second insert is ignored); it costs one extra call. Closing the
  gap needs a shared lock, for example PostgreSQL advisory locks. The container
  runs a single worker for this reason.
- **A narrow window remains within one process.** A request that reads the
  cache after another request's transformer call has finished but before its
  result is committed makes a duplicate call. Same consequence as above.
- **No cap on concurrent transformer calls.** All misses of a request are sent
  at once. A real external service would need a concurrency limit.
- **One failed transformer call fails the request**, and the other results of
  that batch are not stored. The stand-in never fails; a real client would need
  retries and partial saving.
- **No authentication, expiry or deletion.** Cached results and payloads are
  kept forever, which matches the task but would need a retention policy in
  production.
