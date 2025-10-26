FROM python:3.10-slim-bookworm

# Install system deps
RUN apt update && apt install -y --no-install-recommends git supervisor \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -U pip && pip install --no-cache-dir -r requirements.txt

# Copy bot
WORKDIR /Kuttu2DB
COPY . .

# Supervisor config
RUN mkdir -p /etc/supervisor/conf.d
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Healthcheck - bot must respond on localhost:8000 or remove if not needed
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD pgrep -f "python bot.py" || exit 1

# Start Supervisor (auto restarts bot on crash)
CMD ["/usr/bin/supervisord", "-n"]
