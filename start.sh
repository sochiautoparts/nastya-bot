#!/bin/bash
# Nastya Bot — Local development startup script
# Starts Ollama + pulls models + runs the bot
# v26.0: NO QWEN — phi4-mini (text) + moondream (vision)

set -e

echo "=== Nastya Bot 26.0 Local Development (NO QWEN) ==="

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

# Pull required models — phi4-mini (text) + moondream (vision)
echo "Pulling models (if not cached)..."
ollama pull phi4-mini:3.8b
echo "Pulling moondream (2-3x faster vision than qwen3-vl!)..."
ollama pull moondream

echo "Models available:"
ollama list

echo "=== Starting bot ==="
python3 -m bot.main
