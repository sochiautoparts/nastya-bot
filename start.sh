#!/bin/bash
# Nastya Bot — Local development startup script
# Starts Ollama + pulls models + runs the bot

set -e

echo "=== Nastya Bot Local Development ==="

# Install Ollama if not found
if ! command -v ollama &> /dev/null; then
    echo "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
fi

# Start Ollama server if not running
if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "Starting Ollama server..."
    ollama serve &
    sleep 5
fi

# Pull required models
echo "Pulling models (if not cached)..."
ollama pull phi4-mini:3.8b || true
ollama pull qwen3-vl:2b
ollama pull qwen3:1.7b || true

echo "Models available:"
ollama list

echo "=== Starting bot ==="
python3 -m bot.main
