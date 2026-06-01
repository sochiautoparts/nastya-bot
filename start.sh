#!/bin/bash
# Nastya Bot v34 — SMART MODEL AUTO-DETECTION
# Модели автоматически определяются из установленных!
# Приоритет: qwen2.5:1.5b > qwen3:4b-instruct
# vikhr-1B УБРАН — генерирует бред на русском

set -e

echo "=== Nastya Bot v34 (SMART AUTO-DETECT) ==="

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

# ── Pull required models with RETRIES ──
# v34: Более надёжная установка моделей

pull_model() {
    local model="$1"
    local max_retries=3
    local retry=0

    while [ $retry -lt $max_retries ]; do
        echo "Pulling $model (attempt $((retry+1))/$max_retries)..."
        if ollama pull "$model" 2>&1; then
            echo "SUCCESS: $model pulled"
            return 0
        fi
        retry=$((retry+1))
        if [ $retry -lt $max_retries ]; then
            echo "RETRY: waiting 10s before next attempt..."
            sleep 10
        fi
    done

    echo "WARNING: Failed to pull $model after $max_retries attempts"
    return 1
}

# Primary model — qwen2.5:1.5b (0.9GB, fast, good Russian)
pull_model "qwen2.5:1.5b" || echo "WARNING: qwen2.5:1.5b not available, will use qwen3:4b-instruct"

# Reserve model — qwen3:4b-instruct (2.5GB, smarter, slower)
pull_model "qwen3:4b-instruct" || echo "WARNING: qwen3:4b-instruct not available!"

# Verify models
echo ""
echo "=== Installed models ==="
ollama list
echo ""

# Check that at least one model is available
MODEL_COUNT=$(ollama list 2>/dev/null | grep -c "^" || echo "0")
if [ "$MODEL_COUNT" -le 1 ]; then
    echo "ERROR: No models installed! Bot will not work properly."
    echo "Trying to pull qwen2.5:1.5b one more time..."
    ollama pull qwen2.5:1.5b || true
fi

echo "=== Starting bot ==="
python3 -m bot.main
