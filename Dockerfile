FROM python:3.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /Kuttu2DB

# Install runtime deps (git kept for git+ entries in requirements)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /Kuttu2DB/requirements.txt

# Install Python packages
RUN pip install --no-cache-dir -U pip setuptools wheel \
    && pip install --no-cache-dir -r /Kuttu2DB/requirements.txt

# Copy app
COPY . /Kuttu2DB

# Expose port (Koyeb injects $PORT at runtime)
EXPOSE 8080

# Run the single-process bot which starts aiohttp itself.
CMD ["python3", "bot.py"]
