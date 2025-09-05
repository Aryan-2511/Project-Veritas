# Dockerfile.service - reusable service image for Veritas microservices
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install minimal system deps
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential \
      curl \
      ca-certificates \
      netcat-openbsd \
 && rm -rf /var/lib/apt/lists/*

# Copy project files (copy entire repo for simplicity)
COPY . /app

# Install Python deps if a requirements.txt exists in the build context
ARG REQUIREMENTS=./requirements.txt
RUN if [ -f "$REQUIREMENTS" ]; then \
      pip install --no-cache-dir -r $REQUIREMENTS ; \
    else \
      echo "No requirements file at $REQUIREMENTS; skipping pip install"; \
    fi

# Ensure entrypoint exists and is executable
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Prepare data dir for sqlite mount
RUN mkdir -p /app/data

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "concierge.main:app", "--host", "0.0.0.0", "--port", "8080"]
