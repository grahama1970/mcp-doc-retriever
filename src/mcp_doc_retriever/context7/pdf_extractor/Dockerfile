# Use a Python 3.10 slim base image
FROM python:3.10-slim

# Set environment variables to prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies for Poppler, Tesseract, Ghostscript, OpenCV
RUN apt-get update && apt-get install -y \
    poppler-utils \
    ghostscript \
    tesseract-ocr \
    libtesseract-dev \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    build-essential \
    libjpeg-dev \
    zlib1g-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Create directory to store files
RUN mkdir -p /app/files

# Copy the pdf_extractor directory (excluding data directories)
COPY config.py utils.py table_extraction.py marker_processor.py qwen_processor.py \
     pdf_converter.py pdf_to_json_converter.py api.py requirements.txt /app/

# Expose the port FastAPI will run on
EXPOSE 5000

# Run the FastAPI server with Gunicorn
CMD ["gunicorn", "api:app", "-b", "0.0.0.0:5000", "--workers", "2", "--worker-class", "uvicorn.workers.UvicornWorker", "--timeout", "300"]
