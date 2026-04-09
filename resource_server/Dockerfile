FROM python:3.10.20-alpine

WORKDIR /src

COPY resource_server/pyproject.toml resource_server/uv.lock resource_server/.python-version /src/resource_server/
COPY auxillary /src/auxillary

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
RUN uv sync --no-install-project --frozen --no-dev --project /src/resource_server

COPY resource_server /src/resource_server

ENV FLASK_PORT=80
EXPOSE 80

CMD [ "uv", "run", "gunicorn", "-c", "/src/resource_server/gunicorn.conf.py", "app:create_app()"]