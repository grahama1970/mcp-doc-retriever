# Use official Python 3.10 slim image
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install uv package manager
RUN pip install --no-cache-dir uv

# Copy dependency files first for better caching
COPY . .

# Install Python dependencies using uv
RUN uv sync --frozen

# Disable apt signature verification errors workaround
RUN echo 'Acquire::AllowInsecureRepositories "true";' > /etc/apt/apt.conf.d/99insecure \
 && echo 'Acquire::AllowDowngradeToInsecureRepositories "true";' >> /etc/apt/apt.conf.d/99insecure

# Playwright is installed via uv sync from pyproject.toml, removing redundant install step

# Install Playwright browsers and their OS dependencies using uv run
RUN uv run python -m playwright install --with-deps chromium
# Copy the rest of the application code
COPY . .

# Expose port 8000 for the FastAPI app
EXPOSE 8000

# Define a volume for downloads
VOLUME ["/app/downloads"]

# Set the entrypoint to run the FastAPI app with uvicorn
CMD ["uv", "run", "--", "python", "-m", "uvicorn", "src.mcp_doc_retriever.main:app", "--host", "0.0.0.0", "--port", "8000"]