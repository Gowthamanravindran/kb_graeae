# kb-registry: shared store for analysed branches. Build from the repo root:
#   docker build -f deploy/registry/Dockerfile -t kb-registry .
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --extra registry --frozen --no-dev \
 && useradd --system --create-home --uid 10001 kb

USER kb
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/v1/auth/config', timeout=4)"
CMD ["sh", "-c", "kb-registry migrate && kb-registry serve --host 0.0.0.0 --port 8080"]
