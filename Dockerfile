FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    wget \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install playwright browsers (for TikTokApi if needed)
# Note: TikTokApi requires playwright, but we're using HTML parsing instead
# RUN playwright install chromium || true

# Copy application files
COPY . .

# Create necessary directories
RUN mkdir -p /app/data /app/downloads /app/logs

# Set permissions
RUN chmod +x *.py

# Create non-root user
RUN useradd -m -u 1000 tiktokbot && \
    chown -R tiktokbot:tiktokbot /app

# Switch to non-root user
USER tiktokbot

# Expose port (if needed for health checks)
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python3 -c "import requests; import os; token = os.getenv('TELEGRAM_BOT_TOKEN', ''); requests.get(f'https://api.telegram.org/bot{token}/getMe', timeout=5)" || exit 1

# Run the application
CMD ["python3", "run.py"]
