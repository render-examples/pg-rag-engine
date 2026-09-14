#!/usr/bin/env bash
# Runtime wrapper; build-generated defaults come from rag-engine.yaml.
set -euo pipefail

if [[ -f /models/model.env ]]; then
  # shellcheck disable=SC1091
  source /models/model.env
fi

PORT="${PORT:-10000}"
API_KEY="${EMBEDDING_API_KEY:-}"
LLAMA_SERVER="${LLAMA_SERVER:-/app/llama-server}"
MODEL="${EMBEDDING_MODEL:-model}"
POOLING="${EMBEDDING_POOLING:-mean}"
CONTEXT="${EMBEDDING_CONTEXT_TOKENS:-2048}"

ARGS=(
  --model /models/model.gguf
  --embeddings
  --pooling "${POOLING}"
  --alias "${MODEL}"
  -c "${CONTEXT}"
  -ub "${CONTEXT}"
  --host 0.0.0.0
  --port "${PORT}"
)

if [[ -n "${API_KEY}" ]]; then
  ARGS+=(--api-key "${API_KEY}")
fi

exec "${LLAMA_SERVER}" "${ARGS[@]}"
