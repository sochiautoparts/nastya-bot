#!/bin/bash
# Nastya Bot v36 — LLAMA-CPP-PYTHON NATIVE!
# GGUF модель загружается ПРЯМО в процесс — нет Ollama сервера!
# Qwen3-4B-Instruct Q4_K_M — лучший баланс качества/скорости
# AVX2 ускорение — в 2-3x быстрее Ollama на CPU

set -e

echo "=== Nastya Bot v36 (LLAMA-CPP-PYTHON) ==="

# ── Install llama-cpp-python with AVX2 acceleration ──
if ! python3 -c "import llama_cpp" 2>/dev/null; then
    echo "Installing llama-cpp-python with AVX2 support..."
    CMAKE_ARGS="-DGGML_AVX2=on" pip install llama-cpp-python 2>&1 || {
        echo "WARNING: AVX2 build failed, trying without..."
        pip install llama-cpp-python 2>&1 || {
            echo "ERROR: Failed to install llama-cpp-python!"
            exit 1
        }
    }
fi

echo "llama-cpp-python: $(python3 -c 'import llama_cpp; print(llama_cpp.__version__)' 2>/dev/null || echo 'not installed')"

# ── Download GGUF model if not present ──
MODEL_DIR="models"
MODEL_FILE="Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
MODEL_PATH="${MODEL_DIR}/${MODEL_FILE}"

if [ ! -f "$MODEL_PATH" ] || [ ! -s "$MODEL_PATH" ]; then
    echo "Downloading Qwen3-4B-Instruct Q4_K_M model (~2.4GB)..."
    mkdir -p "$MODEL_DIR"

    # Try huggingface_hub first (most reliable)
    python3 -c "
from huggingface_hub import hf_hub_download
path = hf_hub_download(
    repo_id='unsloth/Qwen3-4B-Instruct-2507-GGUF',
    filename='${MODEL_FILE}',
    local_dir='${MODEL_DIR}',
)
print('Downloaded to:', path)
" 2>&1 || {
        echo "WARNING: huggingface_hub download failed, trying wget..."
        # Fallback: direct download
        wget -q --show-progress \
            "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/${MODEL_FILE}" \
            -O "$MODEL_PATH" 2>&1 || {
            echo "ERROR: Failed to download model!"
            exit 1
        }
    }

    # Verify model file
    if [ ! -s "$MODEL_PATH" ]; then
        echo "ERROR: Model file is empty!"
        rm -f "$MODEL_PATH"
        exit 1
    fi
fi

MODEL_SIZE=$(du -h "$MODEL_PATH" | cut -f1)
echo "Model: ${MODEL_FILE} (${MODEL_SIZE})"

# ── Install Python dependencies ──
echo "Installing Python dependencies..."
pip install -r requirements.txt 2>&1 || {
    echo "WARNING: Some dependencies may have issues, continuing..."
}

# ── Create data directory ──
mkdir -p data

# ── Start bot ──
echo "=== Starting bot ==="
python3 -m bot.main
