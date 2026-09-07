# Backend only — the frontend (frontend/) is a static SPA built separately
# (see frontend/Dockerfile or docker-compose.yml) and served by any static
# host; bundling it into the same image would gain nothing since they scale
# and deploy independently.
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir -e .

ENV DATABASE_URL=/data/workflow_engine.db \
    LLM_PROVIDER=mock \
    LOG_LEVEL=INFO

VOLUME ["/data"]
EXPOSE 8000

CMD ["uvicorn", "workflow_engine.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
