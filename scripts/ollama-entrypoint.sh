#!/bin/sh
# Start Ollama; pull default model if OLLAMA_PULL_MODEL is set.
# File must use LF line endings (not CRLF) — see .gitattributes.

ollama serve &
SERVER_PID=$!

echo "Waiting for Ollama server to start..."
sleep 5

if [ -n "$OLLAMA_PULL_MODEL" ]; then
    echo "Pulling model: $OLLAMA_PULL_MODEL"
    ollama pull "$OLLAMA_PULL_MODEL"
fi

wait "$SERVER_PID"
