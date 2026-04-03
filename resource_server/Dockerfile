FROM python:3.10.20-slim

WORKDIR /src

COPY . /src/resource_server
COPY ../auxillary /src/auxillary

RUN pip install uv
RUN uv sync --frozen --no-dev --system

ENV FLASK_PORT=8080
EXPOSE 8080

CMD [ "gunicorn"  "-c" "/src/resource_server/gunicorn.conf.py" "app:create_app()"]