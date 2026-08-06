# Production Dockerfile for FastAPI TensorFlow MobileNetV2 Waste Classifier
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

WORKDIR /app

# Install system dependencies needed for Pillow and TensorFlow
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Ensure uploads folder exists
RUN mkdir -p uploads

# Expose port
EXPOSE 8000

# Run FastAPI app with Uvicorn worker
CMD ["uvicorn", "apps.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
