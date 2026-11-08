#!/usr/bin/env bash
# Agentic RAG — local stack control.
#
#   ./control.sh start           build if needed and start every container
#   ./control.sh stop|off        stop all containers (data is kept)
#   ./control.sh restart [svc]   restart all containers or one service
#   ./control.sh status          show container status
#   ./control.sh logs [svc]      follow logs (optionally one service)
#
# Data is stored in named Docker volumes (pgdata, qdrant_data, minio_data,
# redis has no volume). `stop` only stops containers; this script never
# runs `docker compose down -v`, so the data survives stop/start cycles.

set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

COMPOSE=(docker compose -f docker-compose.yml)
dc() { "${COMPOSE[@]}" "$@"; }

ensure_bucket() {
  dc exec -T api python - <<'PY'
import os

import boto3
from botocore.client import Config

client = boto3.client(
    "s3",
    endpoint_url=os.environ["S3_ENDPOINT_URL"],
    aws_access_key_id=os.environ["S3_ACCESS_KEY"],
    aws_secret_access_key=os.environ["S3_SECRET_KEY"],
    region_name=os.environ.get("S3_REGION", "us-east-1"),
    config=Config(signature_version="s3v4"),
)
try:
    client.create_bucket(Bucket=os.environ.get("S3_BUCKET", "agentic-rag"))
    print("bucket created")
except client.exceptions.BucketAlreadyOwnedByYou:
    print("bucket exists")
PY
}

seed_dev() {
  dc exec -T postgres psql -U agentic -d agentic_rag \
    < backend/scripts/seed_dev.sql
  echo "Dev tenant/user/membership seeded."
}

# Rewrite the RAG_EMBEDDING_* / RAG_QDRANT_COLLECTION lines in .env
set_embedding_cfg() {
  local model="$1" base_url="$2" collection="$3" dim="$4" instruction="$5"
  touch .env
  python3 - "$model" "$base_url" "$collection" "$dim" "$instruction" <<'PY'
import sys
from pathlib import Path

model, base_url, collection, dim, instruction = sys.argv[1:6]
values = {
    "RAG_EMBEDDING_PROVIDER": "openai",
    "RAG_EMBEDDING_MODEL": model,
    "RAG_EMBEDDING_DIM": dim,
    "RAG_EMBEDDING_BASE_URL": base_url,
    "RAG_EMBEDDING_QUERY_INSTRUCTION": instruction,
    "RAG_QDRANT_COLLECTION": collection,
}
path = Path(".env")
lines = path.read_text().splitlines() if path.exists() else []
seen: set[str] = set()
out: list[str] = []
for line in lines:
    key = line.split("=", 1)[0]
    if key in values:
        out.append(f"{key}={values[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in values.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out).rstrip() + "\n")
PY
}

usage() {
  cat <<'EOF'
Usage: ./control.sh <command>

  start           Build (if needed) and start all containers
  stop | off      Stop all containers (named volumes are preserved)
  restart [svc]   Restart everything, or a single service
  status          Show container status
  logs [svc]      Follow logs, optionally for one service
  seed            Seed the dev tenant/user/membership (AUTH_DISABLED principal)
  bucket          Create the S3/MinIO bucket if it does not exist
  embeddings BACKEND
                  Switch embedding backend: qwen3 (qwen3-embedding:8b +
                  query instruction, default, accuracy-first), ollama
                  (bge-m3, fastest) or tei (bge-m3-vi-base, GPU).
                  Each has its own Qdrant collection.

Endpoints after start:
  API       http://localhost:8000/docs
  Frontend  http://localhost:3000
  MinIO     http://localhost:9002  (minioadmin / minioadmin)
  Qdrant    http://localhost:6333/dashboard
  Postgres  localhost:5436         (agentic / agentic, db agentic_rag)
EOF
}

CMD="${1:-start}"
if [ "$#" -gt 0 ]; then shift; fi

case "$CMD" in
  start)
    dc up -d --build
    ensure_bucket || echo "WARN: could not create the S3 bucket"
    if grep -qs '^AUTH_DISABLED=true' .env 2>/dev/null; then
      seed_dev || echo "WARN: could not seed the dev tenant"
    fi
    echo
    dc ps
    echo
    echo "API:      http://localhost:8000/docs"
    echo "Frontend: http://localhost:3000"
    echo "MinIO:    http://localhost:9002 (minioadmin/minioadmin)"
    ;;
  stop | off)
    dc stop
    echo "Containers stopped; named volumes preserved."
    ;;
  restart)
    dc restart "$@"
    dc ps
    ;;
  status | ps)
    dc ps
    ;;
  logs)
    dc logs -f --tail=100 "$@"
    ;;
  seed)
    seed_dev
    ;;
  bucket)
    ensure_bucket
    ;;
  embeddings)
    case "${1:-}" in
      qwen3)
        set_embedding_cfg \
          "qwen3-embedding:8b" "http://host.docker.internal:11434/v1" \
          "chunks_qwen3_8b" "4096" \
          "Given a Vietnamese question, retrieve passages that answer the question"
        dc stop tei >/dev/null 2>&1 || true
        dc up -d api worker
        ;;
      ollama)
        set_embedding_cfg \
          "bge-m3" "http://host.docker.internal:11434/v1" "chunks_ollama" \
          "1024" ""
        # TEI is not needed for Ollama embeddings — stop it to free VRAM.
        dc stop tei >/dev/null 2>&1 || true
        dc up -d api worker
        ;;
      tei)
        set_embedding_cfg \
          "Adc05102002/bge-m3-vi-base" "http://tei:80/v1" "chunks_vi" \
          "1024" ""
        dc up -d tei api worker
        ;;
      *)
        echo "Usage: ./control.sh embeddings <qwen3|ollama|tei>" >&2
        exit 1
        ;;
    esac
    echo
    echo "Embedding backend switched. Documents must be (re)ingested into"
    echo "the new collection before they can be retrieved."
    ;;
  -h | --help | help)
    usage
    ;;
  *)
    usage
    exit 1
    ;;
esac
