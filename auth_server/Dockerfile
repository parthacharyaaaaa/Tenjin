FROM python:3.10.20-alpine

RUN apk add --no-cache libpq-dev build-base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /src

COPY auth_server/pyproject.toml auth_server/uv.lock auth_server/.python-version /src/auth_server/

COPY auxillary /src/auxillary

RUN uv sync --frozen --no-dev --project /src/auth_server

ENV PATH="/src/auth_server/.venv/bin:$PATH"
ENV PYTHONPATH="/src:/src/auth_server"

COPY auth_server /src/auth_server
WORKDIR /src/auth_server

ENV FLASK_PORT=8081
EXPOSE 8081

CMD [ "uv", "run", "gunicorn", "-c", "/src/auth_server/gunicorn.conf.py", "auth_app:create_app()"]