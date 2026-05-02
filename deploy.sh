#!/bin/bash
# deploy.sh — atualiza e reinicia o NinoAgent no VPS
# Uso: ./deploy.sh
set -e

echo "==> Pull do repositório"
git pull

echo "==> Rebuild da imagem do app"
docker compose build ninoagent

echo "==> Reiniciando serviços"
docker compose up -d

echo "==> Status"
docker compose ps

echo "==> Logs (últimas 30 linhas)"
docker compose logs --tail=30 ninoagent
