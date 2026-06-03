#!/bin/bash
# Nastya Bot v44 — EXPANDED MULTI-MODEL + INLINE + AI NEWS!
# Pollinations.ai = EXPANDED 10-MODEL (load balanced!)
# NEW v44: grok (1.6s), gpt-5.4-mini (1.9s), llama-scout, qwen-vision
# Models: openai, mistral, gpt-5.4-mini, grok, deepseek, mistral-4, gemma, llama-scout, qwen-vision, openai-fast
# Automatic failover: if one model fails, next one picks up
# INLINE MODE — Настя работает в любом чате через @asnastya_bot!
# AI-POWERED NEWS POSTS — Настя пишет осмысленные посты на основе новостей!
# Qwen3-4B-Instruct = LOCAL FALLBACK (offline reserve)
# AVX2 acceleration — faster inference on CPU

set -e

echo "=== Nastya Bot v44 (EXPANDED MULTI-MODEL + INLINE + AI NEWS!) ==="

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

# ── Download LOCAL FALLBACK model: Qwen3-4B-Instruct ──
MODEL_DIR="models"
MODEL_FILE="Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
MODEL_PATH="${MODEL_DIR}/${MODEL_FILE}"

if [ ! -f "$MODEL_PATH" ] || [ ! -s "$MODEL_PATH" ]; then
    echo "Downloading Qwen3-4B-Instruct Q4_K_M model (~2.4GB) — LOCAL FALLBACK..."
    mkdir -p "$MODEL_DIR"

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
        wget -q --show-progress \
            "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/${MODEL_FILE}" \
            -O "$MODEL_PATH" 2>&1 || {
            echo "ERROR: Failed to download local model!"
            echo "Bot will run in CLOUD-ONLY mode (Pollinations only, no local fallback)"
            rm -f "$MODEL_PATH"
        }
    }

    if [ -f "$MODEL_PATH" ] && [ ! -s "$MODEL_PATH" ]; then
        echo "WARNING: Model file is empty — running in CLOUD-ONLY mode"
        rm -f "$MODEL_PATH"
    fi
fi

if [ -f "$MODEL_PATH" ] && [ -s "$MODEL_PATH" ]; then
    MODEL_SIZE=$(du -h "$MODEL_PATH" | cut -f1)
    echo "LOCAL FALLBACK: ${MODEL_FILE} (${MODEL_SIZE})"
else
    echo "LOCAL FALLBACK: not available — running in CLOUD-ONLY mode (Pollinations)"
fi

# ── Check Pollinations API key ──
if [ -n "$POLLINATIONS_API_KEY" ]; then
    echo "POLLINATIONS: API key configured (EXPANDED 10-MODEL + VISION + INLINE)"
else
    echo "POLLINATIONS: no API key — using anonymous mode (rate limited)"
fi

# ── Install Python dependencies ──
echo "Installing Python dependencies..."
pip install -r requirements.txt 2>&1 || {
    echo "WARNING: Some dependencies may have issues, continuing..."
}

# ── Create data directory ──
mkdir -p data

# ── Start bot ──
echo "=== Starting Nastya Bot v44 (EXPANDED 10-MODEL Pollinations + INLINE + AI NEWS + Qwen3 FALLBACK) ==="
echo "Config: Pollinations=EXPANDED 10-MODEL (openai, mistral, gpt-5.4-mini, grok, deepseek, mistral-4, gemma, llama-scout, qwen-vision, openai-fast)"
echo "Features: inline=yes, vision=yes(6 models), reasoning=openai-large, max_tokens=1000, ai_news=yes, group_chance=50%, load_balancing=yes"
python3 -m bot.main
