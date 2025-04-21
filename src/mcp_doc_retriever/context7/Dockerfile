# Use official Python 3.11 slim image based on Debian Bookworm
FROM python:3.11-slim-bookworm AS base

# Set environment variables for non-interactive installs and Python behavior
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    DEBIAN_FRONTEND=noninteractive \
    # Define standard path for Playwright browsers (optional, but good practice)
    PLAYWRIGHT_BROWSERS_PATH=/home/appuser/.cache/ms-playwright

# Set working directory
WORKDIR /app

# Install essential system packages: git (for app cloning) and jq (for agent KB access)
# Use standard && for chaining commands
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    git \
    jq \
    ghostscript \
    poppler-utils \
    tesseract-ocr \
    libtesseract-dev \
    curl \
    libxrender1 \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency definition files first to leverage Docker cache layer
COPY pyproject.toml uv.lock* ./

# Install uv using pip
RUN pip install --no-cache-dir uv

# Copy the entire project
COPY . .

# Install ALL project Python dependencies using uv, including playwright
# This builds your local package and installs its dependencies
RUN uv pip install --system .

# Install Playwright system dependencies and browsers using the installed package's command
# The --with-deps flag handles installing necessary system libraries via apt inside this command
RUN playwright install --with-deps
# Optional: Verify Playwright installation (useful for debugging)
RUN playwright --version

# Create necessary directories with proper permissions BEFORE switching user
RUN mkdir -p /app/downloads /app/uploads /app/output /app/corrections && \
    chmod 777 /app/downloads /app/uploads /app/output /app/corrections

# --- Security Best Practice: Create and switch to a non-root user ---
RUN useradd --create-home --shell /bin/bash appuser \
    # Create uv cache directory with proper permissions
    && mkdir -p /home/appuser/.cache/uv \
    && chown -R appuser:appuser /home/appuser/.cache \
    # Set ownership of app directory
    && chown -R appuser:appuser /app
WORKDIR /app
USER appuser
# ---

# Expose the port the application listens on (must match uvicorn port)
EXPOSE 5000

# Define the volume mount points within the container
VOLUME ["/app/uploads", "/app/output", "/app/corrections"]

# Define the command to run the application using gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "src.mcp_doc_retriever.context7.api:app"]