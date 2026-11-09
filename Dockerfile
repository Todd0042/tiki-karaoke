FROM python:3.12-slim

# Install system dependencies (ffmpeg is essential for audio/video processing and loudnorm)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Expose standard FastAPI web port
EXPOSE 8000

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PORT=8000 \
    HOST=0.0.0.0

# Run uvicorn server
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
