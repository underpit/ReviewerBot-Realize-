FROM python:3.10.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEBAPP_PORT=8080

RUN addgroup --system app && adduser --system --ingroup app app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data /app/uploads && chown -R app:app /app

VOLUME ["/app/data", "/app/uploads"]

USER app

EXPOSE 8080

CMD ["python", "main.py"]
