FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# The SQLite file is the cache, so it lives on a volume and outlives the
# container. The directory is handed to an unprivileged user so the service
# does not need root to write to it.
RUN useradd --create-home app \
    && mkdir /data \
    && chown app:app /data
USER app
ENV PAYLOAD_CACHE_DATABASE_URL=sqlite+aiosqlite:////data/payload_cache.db
VOLUME /data

EXPOSE 8000

# The slim image has no curl, so the check uses the Python that is already there.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]

# A single worker on purpose: in-flight transformer calls are shared per
# process, and SQLite allows one writer at a time.
CMD ["uvicorn", "payload_cache.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
