FROM python:3.10-slim

# Create a non-root group and user
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -m -s /bin/bash appuser

# Install system dependencies (build-essential needed for compiling C-extensions like jq)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for caching
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy only the required application files and folders
COPY demo_scenarios/ ./demo_scenarios/
COPY models/ ./models/
COPY routes/ ./routes/
COPY services/ ./services/
COPY utils/ ./utils/
COPY workflows/ ./workflows/
COPY server.py .

# Expose backend port
EXPOSE 8000

# Set ownership of the app directory to the non-root user
RUN chown -R appuser:appgroup /app

# Switch to the non-root user
USER appuser

# Launch the FastAPI app with uvicorn
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]
