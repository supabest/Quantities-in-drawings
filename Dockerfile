FROM python:3.9-slim

WORKDIR /app

# Install system dependencies for OpenCV/Pillow
# 包名按 Debian trixie(13，python:3.9-slim 当前基座)更正：
#   libgl1-mesa-glx 已被移除 -> libgl1；libglib2.0-0 已改名 -> libglib2.0-0t64
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0t64 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create upload and reports directories
RUN mkdir -p app/static/uploads app/static/reports

# Expose port
EXPOSE 5000

# Run with gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "120", "run:app"]
