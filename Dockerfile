FROM python:3.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system deps (supervisor for runtime only)
RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /Kuttu2DB
RUN mkdir -p /Kuttu2DB/logs /var/log/supervisor

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -U pip && pip install --no-cache-dir -r requirements.txt

COPY . .

# Copy Supervisor configs
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY supervisor_programs.conf /etc/supervisor/conf.d/supervisor_programs.conf

EXPOSE 8080

# KEEP SUPERVISOR IN FOREGROUND (IMPORTANT FOR KOYEB)
CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
