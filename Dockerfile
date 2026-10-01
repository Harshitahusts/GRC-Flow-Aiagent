# GRC-Flow API and worker (same image, different command).
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY grc_flow ./grc_flow
RUN pip install --no-cache-dir ".[postgres]"
RUN useradd --create-home --uid 10001 grc && mkdir -p /app/var /app/corpus && chown -R grc /app
USER grc
ENV PORT=8080 HOST=0.0.0.0
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health')"
CMD ["grc-flow", "serve"]
