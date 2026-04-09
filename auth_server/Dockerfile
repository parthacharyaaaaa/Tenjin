FROM python:3.10.20-alpine

WORKDIR /src

COPY auth_server/pyproject.toml auth_server/uv.lock auth_server/.python-version /src/auth_server/
COPY auxillary /src/auxillary

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
RUN uv sync --no-install-project --frozen --no-dev --project /src/auth_server

COPY auth_server /src/auth_server

ENV FLASK_PORT=8080
EXPOSE 8080

CMD [ "uv", "run", "gunicorn", "-c", "/src/auth_server/gunicorn.conf.py", "auth_app:create_app()"]