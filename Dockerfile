# Pinned on Oct 17, 2026 to guarantee build reproducibility
FROM falkordb/falkordb@sha256:b0bdcac2cbaff989e77cb0610439b7a9289e86de033ccbbd4368b66fb680ae7a

ENV PYTHONUNBUFFERED=1 \
    PORT=8000 \
    FALKORDB_HOME=/var/lib/falkordb \
    FALKORDB_DATA_PATH=/var/lib/falkordb/data \
    FALKORDB_BIN_PATH=/var/lib/falkordb/bin

# Install Python 3, pip, and curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python-is-python3 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency specifications first for Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Copy application source code
COPY . .

# Ensure start.sh has execution permissions
RUN chmod +x start.sh

EXPOSE 8000

ENTRYPOINT ["./start.sh"]
