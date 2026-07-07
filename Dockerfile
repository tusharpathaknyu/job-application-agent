FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8787 \
    HOSTED_MODE=true \
    DATABASE_PATH=/var/data/job_agent.db \
    ARTIFACT_DIR=/var/data/packages

WORKDIR /app

COPY pyproject.toml README.md ./
COPY job_agent ./job_agent
COPY profile ./profile
COPY scripts ./scripts

RUN python -m pip install --upgrade pip \
    && python -m pip install -e ".[pdf]"

RUN mkdir -p /var/data/packages

EXPOSE 8787

CMD ["python", "-m", "job_agent", "serve"]
