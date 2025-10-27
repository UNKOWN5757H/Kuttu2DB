FROM python:3.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /Kuttu2DB

# Install runtime deps + git for git+requirements if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /Kuttu2DB/requirements.txt

# Install Python packages
RUN pip install --no-cache-dir -U pip setuptools wheel \
    && pip install --no-cache-dir -r /Kuttu2DB/requirements.txt

# Copy app
COPY . /Kuttu2DB

# Expose port (Koyeb sets $PORT at runtime)
EXPOSE 8080

# Use hypercorn to run the Quart app (bot.py defines `app`)
CMD ["hypercorn", "bot:app", "--bind", "0.0.0.0:${PORT:-8080}", "--workers", "1"]
