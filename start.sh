#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Nastya Bot — Local development startup script
# Starts Ollama + pulls models + runs the bot
# ═══════════════════════════════════════════════════════════════

set -e

echo "=== Starting Nastya Bot 15.1 with Qwen3-VL-2B ==="

# Check if Ollama is installed
if ! command -v ollama &> /dev/null; then
    echo "Ollama not found! Installing..."
    curl -fsSL https://ollama.com/install.sh | sh
fi

# Start Ollama server if not running
if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "Starting Ollama server..."
    ollama serve &
    sleep 5
fi

echo "Ollama status:"
ollama list 2>/dev/null || echo "  Checking models..."

# Pull models if not installed
echo "=== Ensuring models are available ==="
ollama pull qwen3-vl:2b 2>/dev/null || echo "qwen3-vl:2b already available"
ollama pull qwen3:1.7b 2>/dev/null || echo "qwen3:1.7b already available"

echo "=== Launching bot ==="
python3 -m bot.main
