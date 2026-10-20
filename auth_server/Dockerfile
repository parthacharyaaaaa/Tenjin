FROM python:3.13.5-alpine

# System binaries and packages
RUN apk add --no-cache libpq-dev build-base
COPY --from=ghcr.io/astral-sh/uv:0.10.9 /uv /uvx /bin/

WORKDIR /src

# Get uv workspace metadata
COPY pyproject.toml uv.lock .python-version ./
# Copy source files
COPY auxillary ./auxillary
COPY auth_server ./auth_server

# Initialize uv project from copied files and workspace metadata
RUN uv sync --locked --no-dev --package auth_server
ENV PATH="/src/.venv/bin:$PATH"

# Fail-fast on improper installation
RUN python -c "import auxillary; import auth_server"

EXPOSE 8080
CMD ["uvicorn", "main:app", "--workers", "1", "--host", "0.0.0.0", "--port", "8080"]
