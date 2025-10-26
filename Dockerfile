
FROM python:3.10.8-slim-buster

# Install necessary system packages
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -U pip && \
    pip3 install --no-cache-dir -r requirements.txt

# Set working directory and copy bot files
WORKDIR /Kuttu2DB
COPY . .

# Run the bot
CMD ["python", "bot.py"]
