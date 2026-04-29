FROM python:3.13 AS builder

WORKDIR /src
COPY . .

RUN python -m pip install --no-cache-dir -U pip uv \
    && uv venv /opt/venv \
    && . /opt/venv/bin/activate \
    && uv sync --active --locked --no-dev --no-editable

FROM python:3.13-slim
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /src/scripts/docker-entrypoint.sh /entrypoint.sh

ENV TZ="Asia/Shanghai"
ENV EK_IN_DOCKER="1"

WORKDIR /app
RUN chmod +x /entrypoint.sh \
    && touch config.toml
ENV PATH="/opt/venv/bin:$PATH"

ENTRYPOINT ["/entrypoint.sh"]
