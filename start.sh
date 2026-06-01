#!/bin/bash
# Nastya Bot v35 — RSS-FIRST, NO AI for news!
# Новости через RSS + шаблоны. AI только для чата.
# Модели автоматически определяются из установленных!
# Приоритет: qwen2.5:1.5b > qwen3:4b-instruct
# vikhr-1B УБРАН — генерирует бред на русском

set -e

echo "=== Nastya Bot v35 (RSS-FIRST) ==="

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
# v35: Улучшенная установка — больше попыток, лучше логирование

pull_model() {
    local model="$1"
    local max_retries=5
    local retry=0

    # Check if already installed
    if ollama list 2>/dev/null | grep -q "$model"; then
        echo "OK: $model already installed"
        return 0
    fi

    while [ $retry -lt $max_retries ]; do
        echo "Pulling $model (attempt $((retry+1))/$max_retries)..."
        if ollama pull "$model" 2>&1; then
            # Verify it's actually installed
            if ollama list 2>/dev/null | grep -q "$model"; then
                echo "SUCCESS: $model pulled and verified"
                return 0
            else
                echo "WARNING: $model pull seemed OK but not in list. Retrying..."
            fi
        fi
        retry=$((retry+1))
        if [ $retry -lt $max_retries ]; then
            local wait_time=$((10 + retry * 5))
            echo "RETRY: waiting ${wait_time}s before next attempt..."
            sleep $wait_time
        fi
    done

    echo "WARNING: Failed to pull $model after $max_retries attempts"
    return 1
}

# Primary model — qwen2.5:1.5b (0.9GB, fast, good Russian)
# Это ЛУЧШАЯ модель для чата на CPU — быстрая и хороший русский
pull_model "qwen2.5:1.5b" || echo "WARNING: qwen2.5:1.5b not available, will use qwen3:4b-instruct"

# Reserve model — qwen3:4b-instruct (2.5GB, smarter, slower)
# Используется если qwen2.5:1.5b не установлена
pull_model "qwen3:4b-instruct" || echo "WARNING: qwen3:4b-instruct not available!"

# Verify at least one model is available
echo ""
echo "=== Installed models ==="
ollama list
echo ""

MODEL_COUNT=$(ollama list 2>/dev/null | grep -c "^" || echo "0")
if [ "$MODEL_COUNT" -le 1 ]; then
    echo "ERROR: No models installed! Bot will not work properly."
    echo "Trying to pull qwen2.5:1.5b one more time..."
    ollama pull qwen2.5:1.5b || true
fi

# Create data directory for JSON cache
mkdir -p data

echo "=== Starting bot ==="
python3 -m bot.main
