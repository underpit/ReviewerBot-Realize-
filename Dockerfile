# Base image
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WEBAPP_HOST=0.0.0.0 \
    WEBAPP_PORT=8080 \
    RB_REVIEWS_DB=/app/data/reviews.db

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Runtime dirs for SQLite DB and uploads; drop privileges
RUN set -eux; \
    mkdir -p /app/data /app/uploads; \
    adduser --disabled-password --gecos "" appuser; \
    chown -R appuser:appuser /app

USER appuser

VOLUME ["/app/data", "/app/uploads"]

# Expose webapp port (bot still uses long polling)
EXPOSE 8080

CMD ["python3", "main.py"]
