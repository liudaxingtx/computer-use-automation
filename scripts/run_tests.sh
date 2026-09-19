#!/usr/bin/env bash
# 一键跑全部测试（Phase 2~5）。从仓库根目录运行，或直接 ./scripts/run_tests.sh
#
# 会真实调用 DeepSeek（discovery 决策）和 K3（视觉兜底），所以会消耗少量 token。
# 这是题目要求的「真实 LLM run」，不是 mock 出来的假结果。
set -e
cd "$(dirname "$0")/.."

# 1. 确保 mock 应用在跑（没跑就后台拉起来）
STARTED_MOCK=0
if ! curl -s -o /dev/null http://localhost:9000/; then
    echo "▶ 启动 mock 应用 (localhost:9000)..."
    python3 mock-app/server.py > /tmp/mock.log 2>&1 &
    STARTED_MOCK=1
    sleep 1
fi
echo "✓ mock 在线: $(curl -s -o /dev/null -w '%{http_code}' http://localhost:9000/)"
echo

run() {
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "▶ $1"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    if .venv/bin/python -m "$2"; then
        echo "✅ PASS: $1"
    else
        echo "❌ FAIL: $1"
        FAILED=1
    fi
    echo
}

FAILED=0

run "Phase 4 — 确定性回放（三态: success/business/failure）" agent.test_replay
run "Phase 5 — 安全（加密/allowlist/handoff）"             agent.test_phase5
run "Phase 3 — artifact 序列化"                             agent.test_artifact
run "Phase 2 — K3 视觉兜底"                                 agent.test_vision_fallback
run "Phase 6 — 证据 & 可观测性（ReplayStore/遥测/inbox）"   agent.test_observability

# 2. 如果是本脚本拉起的 mock，测完关掉
if [ "$STARTED_MOCK" = "1" ]; then
    pkill -f "mock-app/server.py" 2>/dev/null || true
    echo "▶ 已关闭临时 mock"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ "$FAILED" = "1" ]; then
    echo "❌ 有测试失败，往上翻看具体输出"
    exit 1
else
    echo "✅ 全部通过"
fi
