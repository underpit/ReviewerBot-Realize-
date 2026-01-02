# Base image
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WEBAPP_HOST=0.0.0.0 \
    WEBAPP_PORT=8080

WORKDIR /app

# Install dependencies
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Ensure uploads dir exists for photos
RUN mkdir -p /app/uploads

# Expose webapp port (bot still uses long polling)
EXPOSE 8080

CMD ["python3", "main.py"]
