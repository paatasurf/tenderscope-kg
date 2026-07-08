FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir ".[postgres,rest]"

COPY . .

CMD ["tkg-mcp", "--repo", "/app", "--no-index", "--transport", "sse"]
