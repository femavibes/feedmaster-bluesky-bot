FROM python:3.10-slim

WORKDIR /app

# Install system dependencies including fonts
RUN apt-get update && apt-get install -y \
    fonts-dejavu-core \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY bot.py config_server.py ./

# Run bot
CMD ["python", "bot.py"]