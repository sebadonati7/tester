FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/workspace

WORKDIR /workspace

# Installazione dipendenze di sistema e NGROK
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    ca-certificates \
    gnupg \
    && curl -s https://ngrok-agent.s3.amazonaws.com/ngrok.asc | tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null \
    && echo "deb https://ngrok-agent.s3.amazonaws.com buster main" > /etc/apt/sources.list.d/ngrok.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends ngrok \
    && rm -rf /var/lib/apt/lists/*

# Copia requirements per sfruttare la cache
COPY requirements.txt /workspace/requirements.txt

# Installazione pacchetti Python
RUN python -m pip install --upgrade pip setuptools wheel && \
    pip install -r /workspace/requirements.txt && \
    pip install fastapi "uvicorn[standard]" requests

# Copia l'intera monorepo (incluso il file .env alla radice)
# NOTA: Assicurati di avere il file .dockerignore per non copiare il venv!
COPY . /workspace

# Apre una shell interattiva all'avvio
CMD ["bash"]