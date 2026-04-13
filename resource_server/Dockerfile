FROM python:3.10.20-alpine

RUN apk add --no-cache libpq-dev build-base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /src

COPY resource_server/pyproject.toml resource_server/uv.lock resource_server/.python-version /src/resource_server/
COPY auxillary /src/auxillary

RUN uv export --frozen --no-dev --project /src/resource_server -o requirements.txt
RUN uv pip install --no-cache-dir -r requirements.txt --system

# ENV PATH="/src/resource_server/.venv/bin:$PATH"
ENV PYTHONPATH="/src:/src/resource_server"

COPY resource_server /src/resource_server
WORKDIR /src/resource_server

ENV FLASK_PORT=8081
EXPOSE 8081

CMD [ "gunicorn", "-c", "/src/resource_server/gunicorn.conf.py", "app:create_app()"]