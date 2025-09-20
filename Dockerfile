# Step 1: Use official Python base image (lightweight, matches your Python 3.11)
FROM python:3.11-slim

# Step 2: Set working directory in container
WORKDIR /app

# Step 3: Copy all files (code, configs, JSON) to container
COPY . .

# Step 4: Install dependencies from requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Step 5: Run the bot (main.py) when container starts
CMD ["python", "main.py"]

ENV DOCKER_BUILDKIT=0