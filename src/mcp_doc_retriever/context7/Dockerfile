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
    && rm -rf /var/lib/apt/lists/*

# Copy dependency definition files first to leverage Docker cache layer
COPY pyproject.toml uv.lock* ./

# Install uv using pip
RUN pip install --no-cache-dir uv

# Copy application source code BEFORE installing the project itself
# Ensure .dockerignore prevents copying .git, .venv, etc.
COPY src ./src

# Install ALL project Python dependencies using uv, including playwright
# This builds your local package and installs its dependencies
# Run this AFTER copying src because 'pip install .' needs the source
RUN uv pip install --system .

# Install Playwright system dependencies and browsers using the installed package's command
# The --with-deps flag handles installing necessary system libraries via apt inside this command
RUN playwright install --with-deps
# Optional: Verify Playwright installation (useful for debugging)
RUN playwright --version

# Create the downloads directory and set broad permissions BEFORE switching user
# This ensures the non-root user can write here, especially important for volumes
RUN mkdir -p /app/downloads && chmod 777 /app/downloads

# Copy other potentially necessary files.
# Ensure comments are on separate lines from COPY commands.
# If you DON'T have config.json or DON'T want it baked in, keep this commented.
# COPY config.json ./config.json
# If you DON'T have .env.example or use env vars exclusively, keep this commented.
# COPY .env.example ./
# Copy lessons learned if it's needed by agents running inside the container
COPY src/mcp_doc_retriever/docs/lessons_learned.json ./src/mcp_doc_retriever/docs/lessons_learned.json

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
EXPOSE 8000

# Define the volume mount point within the container
VOLUME ["/app/downloads"]

# Define the command to run the application using uv run and uvicorn
CMD ["uv", "run", "-m", "uvicorn", "src.mcp_doc_retriever.main:app", "--host", "0.0.0.0", "--port", "8000"]