# Metalens beta image — one image runs both the web (uvicorn) and worker (arq)
# processes; fly.toml selects the command per process. Deps are resolved from the
# committed uv.lock so builds are reproducible.
FROM python:3.12-slim

# uv (fast, lockfile-driven installs)
RUN pip install --no-cache-dir uv

WORKDIR /app

# Install dependencies first (cached until pyproject/lock change), then the source.
COPY pyproject.toml uv.lock ./
COPY paperlens/ ./paperlens/
COPY scripts/ ./scripts/
RUN uv sync --frozen --no-dev

# Put the venv on PATH so `uvicorn` / `arq` / `python` resolve to the app's deps.
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
# Default (web) command; the worker process overrides this in fly.toml.
CMD ["uvicorn", "paperlens.app:app", "--host", "0.0.0.0", "--port", "8000"]
