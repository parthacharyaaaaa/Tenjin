FROM python:3.10.20-slim

WORKDIR /src

COPY auth_server/pyproject.toml auth_server/uv.lock /src/auth_server/
RUN pip install uv --no-cache-dir
RUN uv sync --frozen --no-dev --system

COPY auth_server /src/auth_server
COPY auxillary /src/auxillary

ENV FLASK_PORT=8080
EXPOSE 8080

CMD [ "gunicorn", "-c", "/src/auth_server/gunicorn.conf.py", "auth_app:create_app()"]