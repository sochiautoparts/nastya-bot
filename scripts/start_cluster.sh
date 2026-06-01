#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Nastya Bot Production Cluster — Startup Script v23.0
#
# Запускает кластер Ollama с балансировщиком OLOL.
# На GitHub Actions (2 CPU, 7GB RAM) реалистичен один экземпляр
# Ollama, поэтому скрипт адаптируется к доступным ресурсам.
#
# Использование:
#   ./scripts/start_cluster.sh          # Полный кластер (3 ноды)
#   ./scripts/start_cluster.sh --single # Один экземпляр Ollama
# ═══════════════════════════════════════════════════════════════

set -e

SINGLE_MODE=false
if [ "$1" = "--single" ]; then
    SINGLE_MODE=true
    echo "Running in SINGLE MODE (one Ollama instance)"
fi

echo "🚀 Starting Nastya Bot Production Cluster v23.0"

# ── Очистка старых процессов ──
echo "Cleaning up old processes..."
pkill -f "ollama serve" 2>/dev/null || true
pkill -f "olol" 2>/dev/null || true
sleep 2

# ── Проверка доступной памяти ──
TOTAL_MEM_KB=$(grep MemTotal /proc/meminfo 2>/dev/null | awk '{print $2}' || echo "0")
TOTAL_MEM_GB=$((TOTAL_MEM_KB / 1024 / 1024))
echo "Available memory: ${TOTAL_MEM_GB}GB"

# Автоопределение: если меньше 10GB RAM — один экземпляр
if [ "$TOTAL_MEM_GB" -lt 10 ] 2>/dev/null; then
    echo "⚠️ Low memory (${TOTAL_MEM_GB}GB) — switching to SINGLE MODE"
    SINGLE_MODE=true
fi

if [ "$SINGLE_MODE" = true ]; then
    # ═══════════════════════════════════════════════════
    # SINGLE MODE: Один Ollama на порту 11434
    # ═══════════════════════════════════════════════════
    echo "Starting Ollama (single instance)..."

    OLLAMA_HOST=0.0.0.0:11434 OLLAMA_KEEP_ALIVE=-1 ollama serve > /tmp/ollama-11434.log 2>&1 &
    OLLAMA_PID=$!
    echo "Ollama PID: $OLLAMA_PID"

    # Ожидание запуска
    for i in $(seq 1 30); do
        if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
            echo "Ollama is ready!"
            break
        fi
        echo "Waiting for Ollama... ($i/30)"
        sleep 2
    done

    # Загрузка моделей
    echo "Pulling models..."
    ollama pull qwen3-vl:2b || echo "qwen3-vl:2b pull failed (may already exist)"
    ollama pull qwen3:1.7b || echo "qwen3:1.7b pull failed (may already exist)"

    echo ""
    echo "✅ Single instance cluster started!"
    echo "📍 API endpoint: http://localhost:11434"
    echo ""

else
    # ═══════════════════════════════════════════════════
    # CLUSTER MODE: Три Ollama ноды + OLOL Proxy
    # ═══════════════════════════════════════════════════
    echo "Starting Ollama cluster (3 nodes)..."

    # Запуск трёх нод
    OLLAMA_HOST=0.0.0.0:11435 OLLAMA_KEEP_ALIVE=-1 ollama serve > /tmp/ollama-11435.log 2>&1 &
    OLLAMA_HOST=0.0.0.0:11436 OLLAMA_KEEP_ALIVE=-1 ollama serve > /tmp/ollama-11436.log 2>&1 &
    OLLAMA_HOST=0.0.0.0:11437 OLLAMA_KEEP_ALIVE=-1 ollama serve > /tmp/ollama-11437.log 2>&1 &
    sleep 10

    # Загрузка моделей на все ноды
    echo "Pulling models on all nodes..."
    for port in 11435 11436 11437; do
        curl -s -X POST http://localhost:$port/api/pull -d '{"name": "qwen3-vl:2b"}' > /dev/null &
    done
    wait
    sleep 5

    # Лёгкая модель на fallback ноду
    curl -s -X POST http://localhost:11437/api/pull -d '{"name": "qwen3:1.7b"}' > /dev/null

    # Проверка OLOL
    if command -v olol &> /dev/null; then
        echo "Starting OLOL gRPC servers..."
        olol server --host 0.0.0.0 --port 50051 --ollama-host http://localhost:11435 --async > /tmp/olol-50051.log 2>&1 &
        olol server --host 0.0.0.0 --port 50052 --ollama-host http://localhost:11436 --async > /tmp/olol-50052.log 2>&1 &
        olol server --host 0.0.0.0 --port 50053 --ollama-host http://localhost:11437 --async > /tmp/olol-50053.log 2>&1 &
        sleep 5

        echo "Starting OLOL Proxy..."
        olol proxy \
            --host 0.0.0.0 \
            --port 8000 \
            --servers "localhost:50051,localhost:50052,localhost:50053" \
            --distributed \
            --discovery > /tmp/olol-proxy.log 2>&1 &
        sleep 3

        echo ""
        echo "✅ Full cluster started!"
        echo "📍 API endpoint: http://localhost:8000"
        echo ""
    else
        echo "⚠️ OLOL not installed. Using direct Ollama on port 11435."
        echo ""
        echo "✅ Cluster started (without OLOL)!"
        echo "📍 API endpoint: http://localhost:11435"
        echo ""
        echo "To install OLOL: pip install olol"
    fi
fi

# ── Верификация ──
echo "Verifying cluster..."
ENDPOINT="http://localhost:11434"
if [ "$SINGLE_MODE" = false ]; then
    if command -v olol &> /dev/null; then
        ENDPOINT="http://localhost:8000"
    else
        ENDPOINT="http://localhost:11435"
    fi
fi

echo "Checking endpoint: $ENDPOINT"
curl -s "$ENDPOINT/api/tags" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    models = d.get('models', [])
    print(f'  Models: {len(models)}')
    for m in models:
        print(f'    - {m[\"name\"]} ({m.get(\"size\", 0) // 1024 // 1024}MB)')
except:
    print('  ERROR: Could not parse response')
" 2>/dev/null || echo "  WARNING: Endpoint not responding yet"

echo ""
echo "=== Cluster is ready! ==="
