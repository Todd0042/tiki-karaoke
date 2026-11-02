#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

PID_FILE="$DIR/data/karaoke.pid"

stop_services() {
    echo "🌴 Stopping all Tiki Karaoke services..."
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null || true
            sleep 1
            if kill -0 "$PID" 2>/dev/null; then
                kill -9 "$PID" 2>/dev/null || true
            fi
        fi
        rm -f "$PID_FILE"
    fi

    # Terminate any leftover bore or uvicorn processes from this directory
    pkill -f "bin/bore local" 2>/dev/null || true
    pkill -f "cloudflared tunnel" 2>/dev/null || true
    echo "✅ All services stopped cleanly."
}

status_services() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "🌴 Tiki Karaoke Server is RUNNING (PID: $PID)"
            if pgrep -f "bin/bore" >/dev/null; then
                echo "🌐 Permanent Remote Tunnel (bore) is ACTIVE"
            fi
            return 0
        fi
    fi
    echo "ℹ️ Tiki Karaoke Server is STOPPED"
    return 1
}

case "$1" in
    --stop|stop)
        stop_services
        exit 0
        ;;
    --status|status)
        status_services
        exit $?
        ;;
    --restart|restart)
        stop_services
        sleep 1
        shift || true
        exec ./venv/bin/python main.py "$@"
        ;;
    *)
        exec ./venv/bin/python main.py "$@"
        ;;
esac
