FROM python:3.12-slim

WORKDIR /app

# Dependências do sistema (psycopg2-binary não precisa de libpq, mas fastembed precisa de libs C)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir setuptools && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8002

CMD ["uvicorn", "src.webhook:app", "--host", "0.0.0.0", "--port", "8002"]
