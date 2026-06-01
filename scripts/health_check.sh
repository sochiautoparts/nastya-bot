#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Nastya Bot Health Check — Production Cluster v23.0
#
# Проверка работоспособности всех компонентов кластера.
# Использование: ./scripts/health_check.sh
# ═══════════════════════════════════════════════════════════════

echo "=== Nastya Bot Health Check v23.0 ==="
echo ""

# ── Определяем endpoint ──
ENDPOINT="http://localhost:11434"
if curl -s http://localhost:8000/api/tags > /dev/null 2>&1; then
    ENDPOINT="http://localhost:8000"
    echo "🔍 Detected: OLOL Proxy mode"
elif curl -s http://localhost:11435/api/tags > /dev/null 2>&1; then
    ENDPOINT="http://localhost:11435"
    echo "🔍 Detected: Cluster mode (direct)"
else
    echo "🔍 Detected: Single instance mode"
fi

# ── Проверка кластера ──
echo ""
echo "── Cluster Status ──"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$ENDPOINT/api/tags" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    echo "✅ Cluster: HEALTHY (HTTP $HTTP_CODE)"
    curl -s "$ENDPOINT/api/tags" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    for m in d.get('models', []):
        size_mb = m.get('size', 0) // 1024 // 1024
        print(f'  📦 {m[\"name\"]} ({size_mb}MB)')
except:
    print('  ⚠️ Could not parse model list')
" 2>/dev/null
else
    echo "❌ Cluster: DOWN (HTTP $HTTP_CODE)"
fi

# ── Проверка модели (тестовый запрос) ──
echo ""
echo "── Model Test ──"
RESPONSE=$(curl -s -m 60 -X POST "$ENDPOINT/api/chat" \
    -H "Content-Type: application/json" \
    -d '{"model":"qwen3-vl:2b","messages":[{"role":"user","content":"Скажи ОК"}],"stream":false,"options":{"num_predict":5}}' \
    2>/dev/null)

if echo "$RESPONSE" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    msg = d.get('message', {}).get('content', '')
    if msg:
        print(f'✅ Model response: {msg[:50]}')
    else:
        print('⚠️ Model returned empty response')
except:
    print('❌ Model test failed (invalid response)')
" 2>/dev/null; then
    :
else
    echo "❌ Model test failed (timeout or error)"
fi

# ── Проверка Telegram бота ──
echo ""
echo "── Telegram Bot ──"
if [ -n "$BOT_TOKEN" ] || [ -n "$TELEGRAM_TOKEN" ]; then
    TOKEN="${BOT_TOKEN:-$TELEGRAM_TOKEN}"
    BOT_INFO=$(curl -s -m 10 "https://api.telegram.org/bot${TOKEN}/getMe" 2>/dev/null)
    echo "$BOT_INFO" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    if d.get('ok'):
        r = d.get('result', {})
        print(f'✅ Bot: @{r.get(\"username\", \"unknown\")} (ID: {r.get(\"id\", \"?\")})')
    else:
        print(f'❌ Bot error: {d.get(\"description\", \"unknown\")}')
except:
    print('❌ Could not check bot status')
" 2>/dev/null
else
    echo "⚠️ BOT_TOKEN not set — skipping Telegram check"
fi

# ── Проверка Ollama процессов ──
echo ""
echo "── Ollama Processes ──"
OLLAMA_COUNT=$(pgrep -f "ollama serve" 2>/dev/null | wc -l || echo "0")
if [ "$OLLAMA_COUNT" -gt 0 ]; then
    echo "✅ Ollama processes: $OLLAMA_COUNT"
    for pid in $(pgrep -f "ollama serve" 2>/dev/null); do
        PORT=$(ss -tlnp 2>/dev/null | grep "$pid" | head -1 | awk '{print $4}' | rev | cut -d: -f1 | rev)
        echo "  PID $pid → port ${PORT:-unknown}"
    done
else
    echo "❌ No Ollama processes running!"
fi

# ── Проверка памяти ──
echo ""
echo "── System Resources ──"
TOTAL_MEM=$(grep MemTotal /proc/meminfo 2>/dev/null | awk '{print $2}' || echo "0")
FREE_MEM=$(grep MemAvailable /proc/meminfo 2>/dev/null | awk '{print $2}' || echo "0")
TOTAL_GB=$((TOTAL_MEM / 1024 / 1024))
FREE_GB=$((FREE_MEM / 1024 / 1024))
echo "  Memory: ${FREE_GB}GB free / ${TOTAL_GB}GB total"

CPU_CORES=$(nproc 2>/dev/null || echo "?")
echo "  CPU cores: $CPU_CORES"

echo ""
echo "=== Health Check Complete ==="
