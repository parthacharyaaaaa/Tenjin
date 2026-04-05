FROM python:3.10.20-slim

WORKDIR /src

COPY resource_server/pyproject.toml resource_server/uv.lock /src/resource_server/
RUN pip install uv --no-cache-dir
RUN uv sync --frozen --no-dev --system

COPY resource_server /src/resource_server
COPY auxillary /src/auxillary

ENV FLASK_PORT=80
EXPOSE 80

CMD [ "gunicorn", "-c", "/src/resource_server/gunicorn.conf.py", "app:create_app()"]