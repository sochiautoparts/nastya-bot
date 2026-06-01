#!/bin/bash
# Nastya Bot — Local development startup script
# Starts Ollama + pulls models + runs the bot
# v31.0: CPU-OPTIMIZED — Vikhr-1B (primary) + Qwen3-4B (reserve)

set -e

echo "=== Nastya Bot 31.0 Local Development (CPU-OPTIMIZED) ==="

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

# Pull required models — Vikhr-1B (PRIMARY, fast on CPU) + Qwen3-4B (RESERVE)
echo "Pulling Vikhr-1B (primary — fast Russian model, 0.8GB)..."
ollama pull lakomoor/vikhr-llama-3.2-1b-instruct:1b
echo "Pulling Qwen3-4B-instruct (reserve — smart but slower, 2.5GB)..."
ollama pull qwen3:4b-instruct

echo "Models available:"
ollama list

echo "=== Starting bot ==="
python3 -m bot.main
