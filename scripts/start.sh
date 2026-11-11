#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BACKEND_PORT="${BACKEND_PORT:-8765}"

if [ ! -d "${REPO_ROOT}/app/backend/.venv" ]; then
    echo "Error: backend virtual environment not found."
    echo "Please run ./scripts/install.sh first."
    exit 1
fi

# Recursively get child PIDs of a given PID (cross-platform)
get_children() {
    local parent="$1"
    local children=""
    if command -v pgrep >/dev/null 2>&1; then
        children=$(pgrep -P "$parent" 2>/dev/null || true)
    else
        # macOS / BSD fallback
        children=$(ps -o pid= -ppid "$parent" 2>/dev/null | sed 's/ //g' | grep -v '^$' || true)
    fi
    echo "$children"
}

# Recursively kill a process and all its descendants, then reap it.
# This only touches the tree rooted at the given PID and will not
# affect other unrelated npm/node/electron processes on the machine.
kill_tree() {
    local pid="$1"
    local child
    while IFS= read -r child; do
        if [ -n "$child" ]; then
            kill_tree "$child"
        fi
    done < <(get_children "$pid")
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
}

cleanup() {
    trap - INT TERM EXIT
    echo ""
    echo "Shutting down..."

    if [ -n "${FRONTEND_PID:-}" ]; then
        kill_tree "$FRONTEND_PID"
    fi

    if [ -n "${BACKEND_PID:-}" ]; then
        kill_tree "$BACKEND_PID"
    fi
}

trap 'cleanup; exit 0' INT TERM

# Electron owns the backend lifecycle so Settings can really restart it.
# It validates the venv, starts Python, waits for local models, and shuts down
# its child on quit. An externally started backend can still be attached.
echo "==> Starting MeetingBro desktop and its managed backend..."
cd "${REPO_ROOT}/app/frontend"
npm run dev &
FRONTEND_PID=$!
set +e
wait "${FRONTEND_PID}"
FRONTEND_EXIT=$?
set -e
cleanup
exit "${FRONTEND_EXIT}"
