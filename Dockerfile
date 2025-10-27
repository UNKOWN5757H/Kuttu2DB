# ---------- builder stage ----------
FROM python:3.10-slim-bookworm AS builder
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# Install build deps and git for git+ requirements
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential gcc git libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /wheels

# Copy requirements and build wheels (including git-based packages)
COPY requirements.txt /wheels/requirements.txt
RUN pip install --upgrade pip wheel setuptools \
    && pip wheel --no-deps --wheel-dir=/wheels/wheels -r /wheels/requirements.txt

# ---------- final stage ----------
FROM python:3.10-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# Minimal runtime deps (supervisor + ca-certificates)
RUN apt-get update \
    && apt-get install -y --no-install-recommends supervisor ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create workdir
WORKDIR /Kuttu2DB
RUN mkdir -p /Kuttu2DB/logs /var/log/supervisor

# Copy wheel files from builder and install
COPY --from=builder /wheels/wheels /wheels/wheels
RUN pip install --no-cache-dir /wheels/wheels/* \
    && rm -rf /wheels

# Copy app files
COPY . /Kuttu2DB

# Supervisor configs
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY supervisor_programs.conf /etc/supervisor/conf.d/supervisor_programs.conf

# Expose port (Koyeb injects $PORT at runtime)
EXPOSE 8080

# Run supervisord in foreground
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
