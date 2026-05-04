FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Instala dependências e força setuptools no final (crewai 0.80 depende de pkg_resources)
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir --force-reinstall setuptools

COPY . .

EXPOSE 8002

CMD ["uvicorn", "src.webhook:app", "--host", "0.0.0.0", "--port", "8002"]
