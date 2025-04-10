# Use official Python 3.11 slim image
FROM python:3.11-slim

# --- Install System Dependencies and uv (as root) ---
# Install system dependencies for Playwright using its helper command
RUN apt-get update && \
    apt-get install -y --no-install-recommends python3-pip && \
    pip install playwright==1.51.0 && \
    playwright install-deps && \
    pip uninstall -y playwright && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

# --- Copy ALL necessary files for build ---
COPY pyproject.toml uv.lock* ./
COPY src ./src

# --- Install Dependencies INCLUDING the local package (as root) ---
# Install into system python using --system flag
RUN uv pip install --no-cache-dir --system .

# --- Playwright Python Package Installation ---
# The Playwright *Python package* is installed system-wide via 'uv pip install .', done earlier as root.
# The actual *browser binaries* will be installed later, as 'appuser'.
# (Removed 'RUN playwright install...' command from here)

# --- Create Non-Root User and Set Permissions (as root) ---
# Create downloads dir first
RUN mkdir -p /app/downloads
# Create user/group
# Create user/group with home directory
RUN groupadd --system appuser && \
    useradd --system --gid appuser -m appuser && \
    # Ensure ownership of app and home directory (including downloads within app)
    chown -R appuser:appuser /app && \
    chown -R appuser:appuser /home/appuser

# --- Switch to Non-Root User ---
USER appuser

# --- Optional: Final Code Copy (as appuser) ---
# Ensures files outside src are present and owned by appuser.
# Overwrites previously copied files with correct ownership.
COPY . .

# --- Install Playwright Browsers (as appuser) ---
# Now run install as the appuser to ensure browsers are in the correct user cache
# We run WITHOUT --with-deps because system deps should already be installed via apt-get/playwright install-deps as root.
RUN python -m playwright install chromium

# --- Install Playwright Browsers (as appuser) ---
# Now run install as the appuser to ensure browsers are in the correct user cache
RUN python -m playwright install --with-deps chromium

# --- Runtime Configuration ---
EXPOSE 8000
# Volume mount point permissions are now set during build based on the chown above
VOLUME ["/app/downloads"]

# CORRECTED CMD: Execute uvicorn directly via python -m.
# Packages are installed system-wide, so appuser can run this.
CMD ["python", "-m", "uvicorn", "src.mcp_doc_retriever.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "debug", "--workers", "1"]