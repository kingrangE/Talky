#!/usr/bin/env bash
set -e

MODEL_NAME="${MODEL_NAME:-skt/A.X-4.0-Light}"
WORKDIR="/work"

ollama serve &
OPID=$!

echo "[ollama-init] waiting for server..."
for _ in $(seq 1 60); do
    if ollama list >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

if ollama list | awk 'NR>1 {print $1}' | grep -qx "$MODEL_NAME"; then
    echo "[ollama-init] model $MODEL_NAME already present, skipping create."
elif [ -f "$WORKDIR/Modelfile" ] && ls "$WORKDIR/model"/*.gguf >/dev/null 2>&1; then
    echo "[ollama-init] creating $MODEL_NAME from $WORKDIR/Modelfile..."
    cd "$WORKDIR"
    ollama create "$MODEL_NAME" -f Modelfile
else
    echo "[ollama-init] Modelfile or .gguf not mounted under $WORKDIR — skipping create."
fi

wait "$OPID"
