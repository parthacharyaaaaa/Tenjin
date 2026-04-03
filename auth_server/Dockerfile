FROM python:3.10.20-slim

WORKDIR /src

COPY . /src/auth_server
COPY ../auxillary /src/auxillary

RUN pip install uv
RUN uv sync --frozen --no-dev --system

ENV FLASK_PORT=8090
EXPOSE 8090

CMD [ "gunicorn"  "-c" "/src/auth_server/gunicorn.conf.py" "auth_app:create_app()"]