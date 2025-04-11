# Use official Playwright Python image with browsers and dependencies pre-installed
FROM mcr.microsoft.com/playwright/python:v1.51.0-noble

WORKDIR /app

# Install jq FIRST
RUN apt-get update && apt-get install -y --no-install-recommends jq && rm -rf /var/lib/apt/lists/*

# Copy dependency files first for caching
COPY pyproject.toml uv.lock* ./

# Install uv (if not pre-installed)
RUN pip install --no-cache-dir uv

# Copy source code
COPY src ./src

# Install project dependencies inside container
RUN uv pip install --system .

# Create downloads directory and set permissions
RUN mkdir -p /app/downloads

# Copy remaining project files (config, scripts, etc.)
COPY . .

EXPOSE 8000
VOLUME ["/app/downloads"]

# Run FastAPI app using direct python -m per lessons learned
CMD ["python", "-m", "uvicorn", "src.mcp_doc_retriever.main:app", "--host", "0.0.0.0", "--port", "8000"]