# One image for every service; docker-compose picks the service via `command`.
# Chrome is only needed by crawl_mgt, detect_mgt and probe_mgt, but a single image keeps the build simple.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends wget gnupg ca-certificates \
    && wget -qO- https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
       > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev
COPY . .

ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 DELTER_DATA_DIR=/data
VOLUME /data
CMD ["uvicorn", "--factory", "gateway.gateway_main:create_app", "--host", "0.0.0.0", "--port", "8000"]
