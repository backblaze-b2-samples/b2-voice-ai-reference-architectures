#!/usr/bin/env bash
# Run the TypeScript examples' tests against a local S3-compatible server (moto).
# No B2 account or credentials are needed. Requires Node 22 or later, npm, and
# moto[server] (see requirements-dev.txt); the server comes from .venv when present.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root/examples/typescript"

if [ -x "$root/.venv/bin/moto_server" ]; then
  moto_server_bin="$root/.venv/bin/moto_server"
elif command -v moto_server >/dev/null 2>&1; then
  moto_server_bin="$(command -v moto_server)"
else
  echo "moto_server not found: pip install -r requirements-dev.txt" >&2
  exit 2
fi

npm ci --no-audit --no-fund --loglevel=error

port="$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
log="$(mktemp)"
"$moto_server_bin" --host 127.0.0.1 --port "$port" >"$log" 2>&1 &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true; rm -f "$log"' EXIT

for _ in $(seq 1 100); do
  if python3 -c "import socket,sys; s = socket.socket(); s.settimeout(0.2); sys.exit(0 if s.connect_ex(('127.0.0.1', $port)) == 0 else 1)"; then
    break
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    echo "moto_server exited early:" >&2
    cat "$log" >&2
    exit 1
  fi
  sleep 0.1
done

export MOTO_ENDPOINT="http://127.0.0.1:$port"
npm run --silent typecheck
npm test --silent
