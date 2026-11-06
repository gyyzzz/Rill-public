FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml .
COPY config.toml.example config.toml
COPY src/ src/

# Install Python dependencies
RUN pip install --no-cache-dir -e . \
    && pip install --no-cache-dir gunicorn

# Create workspace directory
RUN mkdir -p /workspace

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health/ready || exit 1

# Run with Gunicorn
CMD ["gunicorn", "rill.main:app", \
     "--workers", "2", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8080", \
     "--timeout", "120"]
