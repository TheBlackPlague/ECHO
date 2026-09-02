#!/bin/sh
set -eu

command="${1:-serve}"
if [ "$#" -gt 0 ]; then
    shift
fi

case "$command" in
    serve)
        ;;
    upgrade-database)
        exec python /opt/echo/scripts/upgrade_database.py "$@"
        ;;
    *)
        exec "$command" "$@"
        ;;
esac

backend_pid=""
frontend_pid=""

shutdown() {
    trap - TERM INT EXIT

    if [ -n "$frontend_pid" ] && kill -0 "$frontend_pid" 2>/dev/null; then
        kill -TERM "$frontend_pid" 2>/dev/null || true
    fi

    if [ -n "$backend_pid" ] && kill -0 "$backend_pid" 2>/dev/null; then
        kill -TERM "$backend_pid" 2>/dev/null || true
    fi

    [ -z "$frontend_pid" ] || wait "$frontend_pid" 2>/dev/null || true
    [ -z "$backend_pid" ] || wait "$backend_pid" 2>/dev/null || true
}

trap shutdown TERM INT EXIT

python -m echo &
backend_pid=$!

python -m http.server 5173 --bind 0.0.0.0 --directory /srv/echo/frontend &
frontend_pid=$!

while kill -0 "$backend_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do
    sleep 1
done

if ! kill -0 "$backend_pid" 2>/dev/null; then
    wait "$backend_pid"
    status=$?
else
    wait "$frontend_pid"
    status=$?
fi

exit "$status"
