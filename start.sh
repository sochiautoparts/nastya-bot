#!/bin/bash
# Nastya Bot v69 — RUADAPT QWEN3-4B-INSTRUCT + LOCAL-ONLY POSTING!
# v69: RuadaptQwen3-4B-Instruct — Russian tokenizer + Instruct (no <think> tags!)
#   - 48K extra Russian tokens → up to 2x faster Russian generation
#   - Instruct: answers DIRECTLY without <think> tags
#   - Russian fine-tuning + LEP (Learned Embedding Propagation)
# Pollinations.ai = EXPANDED 10-MODEL (load balanced!)
# INLINE MODE — Настя работает в любом чате через @asnastya_bot!
# AI-POWERED NEWS POSTS — Настя пишет посты через локальную модель!
# LOCAL MODEL: Enabled by default (ENABLE_LOCAL_MODEL=true)

set -e

echo "=== Nastya Bot v69 (RUADAPT QWEN3-4B-INSTRUCT + LOCAL-ONLY POSTING + SEARCH & DISCOVERY) ==="

# ── Check if local model is enabled ──
ENABLE_LOCAL="${ENABLE_LOCAL_MODEL:-true}"

if [ "$ENABLE_LOCAL" = "true" ] || [ "$ENABLE_LOCAL" = "1" ] || [ "$ENABLE_LOCAL" = "yes" ]; then
    echo "LOCAL MODEL: ENABLED — will load RuadaptQwen3-4B-Instruct (PRIMARY for posting + chat/comments)"
    echo "  Russian tokenizer: 48K extra Russian tokens → up to 2x faster"
    echo "  Instruct version: answers DIRECTLY (no <think> tags!)"
    echo "  Russian fine-tuning + LEP"

    # ── Install llama-cpp-python with AVX2 acceleration ──
    if ! python3 -c "import llama_cpp" 2>/dev/null; then
        echo "Installing llama-cpp-python with AVX2 support..."
        CMAKE_ARGS="-DGGML_AVX2=on" pip install llama-cpp-python 2>&1 || {
            echo "WARNING: AVX2 build failed, trying without..."
            pip install llama-cpp-python 2>&1 || {
                echo "ERROR: Failed to install llama-cpp-python!"
                echo "Continuing in CLOUD-ONLY mode..."
            }
        }
    fi

    echo "llama-cpp-python: $(python3 -c 'import llama_cpp; print(llama_cpp.__version__)' 2>/dev/null || echo 'not installed')"

    # ── Download LOCAL model: RuadaptQwen3-4B-Instruct-Q4_K_M ──
    # Must match config.py MODEL_PATH and MODEL_DOWNLOAD_URL!
    MODEL_DIR="models"
    MODEL_FILE="RuadaptQwen3-4B-Instruct-Q4_K_M.gguf"
    MODEL_PATH="${MODEL_DIR}/${MODEL_FILE}"

    if [ ! -f "$MODEL_PATH" ] || [ ! -s "$MODEL_PATH" ]; then
        echo "Downloading RuadaptQwen3-4B-Instruct Q4_K_M model (~2.5GB) — LOCAL-FIRST..."
        mkdir -p "$MODEL_DIR"

        python3 -c "
from huggingface_hub import hf_hub_download
path = hf_hub_download(
    repo_id='RefalMachine/RuadaptQwen3-4B-Instruct-GGUF',
    filename='Q4_K_M.gguf',
    local_dir='${MODEL_DIR}',
)
# Rename to descriptive name
import shutil, os
src = os.path.join('${MODEL_DIR}', 'Q4_K_M.gguf')
dst = os.path.join('${MODEL_DIR}', '${MODEL_FILE}')
if os.path.exists(src) and src != dst:
    shutil.move(src, dst)
print('Downloaded to:', dst)
" 2>&1 || {
            echo "WARNING: huggingface_hub download failed, trying wget..."
            wget -q --show-progress \
                "https://huggingface.co/RefalMachine/RuadaptQwen3-4B-Instruct-GGUF/resolve/main/Q4_K_M.gguf" \
                -O "$MODEL_PATH" 2>&1 || {
                echo "WARNING: Failed to download local model!"
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
        echo "LOCAL MODEL: ${MODEL_FILE} (${MODEL_SIZE})"
    else
        echo "LOCAL MODEL: not available — running in CLOUD-ONLY mode (Pollinations)"
    fi
else
    echo "LOCAL MODEL: DISABLED (cloud-only mode — faster startup)"
    echo "To enable: set ENABLE_LOCAL_MODEL=true"
fi

# ── Check Pollinations API key ──
if [ -n "$POLLINATIONS_API_KEY" ]; then
    echo "POLLINATIONS: API key configured (EXPANDED 10-MODEL + VISION + INLINE)"
else
    echo "POLLINATIONS: no API key — using anonymous mode (rate limited)"
fi

# ── LOCAL-ONLY POSTING check ──
LOCAL_POSTING="${LOCAL_ONLY_POSTING:-true}"
if [ "$LOCAL_POSTING" = "true" ] || [ "$LOCAL_POSTING" = "1" ]; then
    echo "LOCAL-ONLY POSTING: ENABLED — channel posts via local model (saves cloud limits!)"
else
    echo "LOCAL-ONLY POSTING: DISABLED — channel posts via cloud (uses API limits)"
fi

# ── Install Python dependencies ──
echo "Installing Python dependencies..."
pip install -r requirements.txt 2>&1 || {
    echo "WARNING: Some dependencies may have issues, continuing..."
}

# ── Create data directory ──
mkdir -p data

# ── Start bot ──
echo "=== Starting Nastya Bot v69 (RUADAPT QWEN3-4B-INSTRUCT + LOCAL-ONLY POSTING + 10-MODEL Pollinations + SEARCH + DISCOVERY) ==="
echo "Config: LOCAL-ONLY POSTING=yes, Pollinations=EXPANDED 10-MODEL + SEARCH + DISCOVERY"
echo "Strategy: Local-first (chat/comments) + Local-only (posting) + Cloud (consultations/vision)"
echo "FAILOVER: Local -> Pollinations(key) -> Pollinations(free) -> Cloudflare -> Local(fallback) -> Static"
echo "Local model: $([ "$ENABLE_LOCAL" = "true" ] && echo 'RuadaptQwen3-4B-Instruct' || echo 'DISABLED (cloud-only)')"
echo "Local-only posting: $([ "$LOCAL_POSTING" = "true" ] && echo 'ENABLED' || echo 'DISABLED')"
python3 -m bot.main
