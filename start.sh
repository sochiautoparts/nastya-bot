#!/bin/bash
# Nastya Bot v33 — OLLAMA-FIRST Edition
# qwen2.5:1.5b (primary — best Russian quality/speed for CPU)
# qwen3:4b-instruct (reserve — smarter, slower)
# vikhr-1B УБРАН — генерирует бред на русском

set -e

echo "=== Nastya Bot v33 (OLLAMA-FIRST — qwen2.5:1.5b + qwen3:4b) ==="

# Install Ollama if not found
if ! command -v ollama &> /dev/null; then
    echo "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
fi

# Start Ollama server if not running
if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "Starting Ollama server..."
    OLLAMA_KEEP_ALIVE=-1 ollama serve &
    sleep 5
fi

# Remove old vikhr-1B model (generates garbage in Russian)
echo "Removing old vikhr-1B model (generates garbage)..."
ollama rm lakomoor/vikhr-llama-3.2-1b-instruct:1b 2>/dev/null || true

# Pull required models
echo "Pulling qwen2.5:1.5b (primary — best Russian quality/speed for CPU, 0.9GB)..."
ollama pull qwen2.5:1.5b
echo "Pulling qwen3:4b-instruct (reserve — smarter, slower, 2.5GB)..."
ollama pull qwen3:4b-instruct

echo "Models available:"
ollama list

echo "=== Starting bot ==="
python3 -m bot.main
