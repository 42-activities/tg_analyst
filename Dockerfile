# syntax=docker/dockerfile:1

FROM python:3.12-slim AS build
# Extras to install. `hf` adds CPU-only torch + transformers for multilingual sentiment;
# build with --build-arg EXTRAS=web for a slim image without it.
ARG EXTRAS=web,hf
WORKDIR /src
# CPU-only torch wheels when the hf extra is requested (avoids ~4 GB of CUDA libraries).
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
# Install dependencies first so they are cached across source-only changes.
COPY pyproject.toml README.md ./
RUN mkdir -p src/tg_analyst && touch src/tg_analyst/__init__.py \
    && pip install ".[${EXTRAS}]" && pip uninstall -y tg-analyst
COPY src ./src
RUN pip install --no-deps .


FROM python:3.12-slim AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TG_SESSION_PATH=/app/data/tg_analyst \
    HF_HOME=/app/data/hf
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/data && chown app:app /app/data
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
USER app
# Telegram session files and the model cache live here; mount a volume to persist them.
VOLUME ["/app/data"]
ENTRYPOINT ["tg-analyst"]
CMD ["--help"]
