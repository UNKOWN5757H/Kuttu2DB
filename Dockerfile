FROM python:3.10-slim

# System deps
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential \
       libpq-dev \
       supervisor \
    && rm -rf /var/lib/apt/lists/*

# Create app user and directories
RUN mkdir -p /app /var/log/supervisor /app/logs
WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy project files
COPY . /app

# Ensure logs dir exists and is writable
RUN mkdir -p /app/logs && chown -R root:root /app/logs

# Supervisor config location
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY supervisor_programs.conf /etc/supervisor/conf.d/supervisor_programs.conf

# Expose the port (Koyeb will set PORT env var at runtime)
EXPOSE 8080

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
