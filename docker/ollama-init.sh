#!/usr/bin/env bash
set -e

MODEL_NAME="${MODEL_NAME:-skt/A.X-4.0-Light}"
WORKDIR="/work"

ollama serve &
OPID=$!

echo "[ollama-init] waiting for server..."
SERVER_READY=0
for _ in $(seq 1 60); do
    if ollama list >/dev/null 2>&1; then
        SERVER_READY=1
        break
    fi
    sleep 1
done

if [ "$SERVER_READY" -ne 1 ]; then
    echo "[ollama-init] server did not become ready." >&2
    kill "$OPID" 2>/dev/null || true
    wait "$OPID" 2>/dev/null || true
    exit 1
fi

if ollama show "$MODEL_NAME" >/dev/null 2>&1; then
    echo "[ollama-init] model $MODEL_NAME already present, skipping create."
elif [ -f "$WORKDIR/Modelfile" ] && ls "$WORKDIR/model"/*.gguf >/dev/null 2>&1; then
    echo "[ollama-init] creating $MODEL_NAME from $WORKDIR/Modelfile..."
    cd "$WORKDIR"
    ollama create "$MODEL_NAME" -f Modelfile
else
    echo "[ollama-init] Modelfile or .gguf not mounted under $WORKDIR." >&2
    echo "[ollama-init] place the GGUF referenced by Modelfile in ./model/." >&2
    kill "$OPID" 2>/dev/null || true
    wait "$OPID" 2>/dev/null || true
    exit 1
fi

if ! ollama show "$MODEL_NAME" >/dev/null 2>&1; then
    echo "[ollama-init] model $MODEL_NAME is not available after initialization." >&2
    kill "$OPID" 2>/dev/null || true
    wait "$OPID" 2>/dev/null || true
    exit 1
fi

wait "$OPID"
