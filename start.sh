#!/bin/bash
# Nastya Bot v38 — LLAMA-CPP-PYTHON DUAL-MODEL!
# Qwen3-4B-Instruct = PRIMARY (лучший русский, живые ответы)
# Qwen2.5-3B-Instruct = SECONDARY (лёгкая быстрая резервная)
# AVX2 ускорение — в 2-3x быстрее Ollama на CPU

set -e

echo "=== Nastya Bot v38 (LLAMA-CPP-PYTHON DUAL-MODEL) ==="

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

# ── Download PRIMARY model: Qwen3-4B-Instruct ──
MODEL_DIR="models"
MODEL1_FILE="Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
MODEL1_PATH="${MODEL_DIR}/${MODEL1_FILE}"

if [ ! -f "$MODEL1_PATH" ] || [ ! -s "$MODEL1_PATH" ]; then
    echo "Downloading Qwen3-4B-Instruct Q4_K_M model (~2.4GB)..."
    mkdir -p "$MODEL_DIR"

    python3 -c "
from huggingface_hub import hf_hub_download
path = hf_hub_download(
    repo_id='unsloth/Qwen3-4B-Instruct-2507-GGUF',
    filename='${MODEL1_FILE}',
    local_dir='${MODEL_DIR}',
)
print('Downloaded to:', path)
" 2>&1 || {
        echo "WARNING: huggingface_hub download failed, trying wget..."
        wget -q --show-progress \
            "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/${MODEL1_FILE}" \
            -O "$MODEL1_PATH" 2>&1 || {
            echo "ERROR: Failed to download PRIMARY model!"
            exit 1
        }
    }

    if [ ! -s "$MODEL1_PATH" ]; then
        echo "ERROR: Primary model file is empty!"
        rm -f "$MODEL1_PATH"
        exit 1
    fi
fi

MODEL1_SIZE=$(du -h "$MODEL1_PATH" | cut -f1)
echo "PRIMARY: ${MODEL1_FILE} (${MODEL1_SIZE})"

# ── Download SECONDARY model: Qwen2.5-3B-Instruct ──
MODEL2_FILE="Qwen2.5-3B-Instruct-Q4_K_M.gguf"
MODEL2_PATH="${MODEL_DIR}/${MODEL2_FILE}"

if [ ! -f "$MODEL2_PATH" ] || [ ! -s "$MODEL2_PATH" ]; then
    echo "Downloading Qwen2.5-3B-Instruct Q4_K_M model (~2.0GB)..."
    mkdir -p "$MODEL_DIR"

    python3 -c "
from huggingface_hub import hf_hub_download
path = hf_hub_download(
    repo_id='Qwen/Qwen2.5-3B-Instruct-GGUF',
    filename='${MODEL2_FILE}',
    local_dir='${MODEL_DIR}',
)
print('Downloaded to:', path)
" 2>&1 || {
        echo "WARNING: huggingface_hub download failed, trying wget..."
        wget -q --show-progress \
            "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/${MODEL2_FILE}" \
            -O "$MODEL2_PATH" 2>&1 || {
            echo "WARNING: Failed to download SECONDARY model — bot will run with PRIMARY only"
            rm -f "$MODEL2_PATH"
        }
    }

    if [ -f "$MODEL2_PATH" ] && [ ! -s "$MODEL2_PATH" ]; then
        echo "WARNING: Secondary model file is empty — running with PRIMARY only"
        rm -f "$MODEL2_PATH"
    fi
fi

if [ -f "$MODEL2_PATH" ] && [ -s "$MODEL2_PATH" ]; then
    MODEL2_SIZE=$(du -h "$MODEL2_PATH" | cut -f1)
    echo "SECONDARY: ${MODEL2_FILE} (${MODEL2_SIZE})"
else
    echo "SECONDARY: not available — running with PRIMARY only"
fi

# ── Install Python dependencies ──
echo "Installing Python dependencies..."
pip install -r requirements.txt 2>&1 || {
    echo "WARNING: Some dependencies may have issues, continuing..."
}

# ── Create data directory ──
mkdir -p data

# ── Start bot ──
echo "=== Starting Nastya Bot v38 (Qwen3 + Qwen2.5-3B) ==="
python3 -m bot.main
