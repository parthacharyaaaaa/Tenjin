FROM python:3.13.5-alpine

# System binaries and packages
RUN apk add --no-cache libpq-dev build-base
COPY --from=ghcr.io/astral-sh/uv:0.10.9 /uv /uvx /bin/

WORKDIR /src

# Get uv workspace metadata
COPY pyproject.toml uv.lock .python-version ./
# uv validates the complete workspace against the shared lockfile.
COPY demo_rs/pyproject.toml ./demo_rs/
COPY email_worker/pyproject.toml ./email_worker/
COPY resource_auxillary/pyproject.toml ./resource_auxillary/
COPY resource_database_workers/pyproject.toml ./resource_database_workers/
COPY resource_server/pyproject.toml ./resource_server/
# Copy source files
COPY auxillary ./auxillary
COPY auth_server ./auth_server

# Initialize uv project from copied files and workspace metadata
RUN uv sync --locked --no-dev --package auth-server
ENV PATH="/src/.venv/bin:$PATH"
ENV PYTHONPATH="/src/auth_server/src"

# Fail-fast on improper installation
RUN python -c "import auxillary; import auth_server.main"

EXPOSE 8080
CMD ["uvicorn", "auth_server.main:app", "--workers", "1", "--host", "0.0.0.0", "--port", "8080"]
