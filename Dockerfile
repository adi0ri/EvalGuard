# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm

# Keep the dependency installer deterministic and keep the virtual environment
# on PATH so Compose commands do not need to invoke `uv run` at runtime.
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Install locked runtime dependencies before copying the application so normal
# source edits can reuse this comparatively expensive layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable && \
    addgroup --system evalguard && \
    adduser --system --ingroup evalguard evalguard

USER evalguard

EXPOSE 8000

CMD ["uvicorn", "evalguard.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
