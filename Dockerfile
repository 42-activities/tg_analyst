# syntax=docker/dockerfile:1

FROM python:3.12-slim AS build
WORKDIR /src
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
# Install dependencies first so they are cached across source-only changes.
COPY pyproject.toml README.md ./
RUN mkdir -p src/tg_analyst && touch src/tg_analyst/__init__.py \
    && pip install ".[postgres]" && pip uninstall -y tg-analyst
COPY src ./src
RUN pip install --no-deps ".[postgres]"


FROM python:3.12-slim AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TG_SESSION_PATH=/app/data/tg_analyst \
    DATABASE_URL=sqlite:////app/data/tg_analyst.db
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/data && chown app:app /app/data
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
USER app
# Session files and the SQLite DB live here; mount a volume to persist them.
VOLUME ["/app/data"]
ENTRYPOINT ["tg-analyst"]
CMD ["--help"]
