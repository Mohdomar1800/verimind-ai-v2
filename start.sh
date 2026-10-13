#!/usr/bin/env bash
# Start VeriMind on macOS or Linux:  ./start.sh
set -euo pipefail
cd "$(dirname "$0")"

fail() {
  printf '\n\033[31mVeriMind could not start: %s\033[0m\n\n' "$1" >&2
  exit 1
}

# Opening the browser is a convenience: it must never make a successful start look like a failure
# (servers and CI machines have no browser). `open` is the macOS command; Linux desktops use xdg-open.
open_browser() {
  if [ "$no_browser" = 1 ] || [ -n "${CI:-}" ]; then return 0; fi
  case "$(uname -s)" in
    Darwin) open "$1" >/dev/null 2>&1 ;;
    *) command -v xdg-open >/dev/null 2>&1 && xdg-open "$1" >/dev/null 2>&1 ;;
  esac
}

no_browser=0
[ "${1:-}" = "--no-browser" ] && no_browser=1

echo "Starting VeriMind..."

command -v docker >/dev/null 2>&1 \
  || fail "Docker is not installed. Install Docker Desktop (macOS) or Docker Engine (Linux), then run this again."
if ! docker_info=$(docker info 2>&1); then
  case "$docker_info" in
    *"permission denied"*)
      fail "Your user is not allowed to use Docker. Run: sudo usermod -aG docker \$USER  then log out and back in (or run: sudo ./start.sh)." ;;
    *) fail "Docker is installed but not running. Start Docker, wait until it is ready, then run this again." ;;
  esac
fi
docker compose version >/dev/null 2>&1 \
  || fail "Docker Compose is missing. Install the Docker Compose plugin (it comes with Docker Desktop), then run this again."

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env with default settings."
fi

port=$(sed -n 's/^[[:space:]]*VERIMIND_PORT[[:space:]]*=[[:space:]]*\([0-9][0-9]*\).*/\1/p' .env | head -n 1)
url="http://localhost:${port:-8000}"
# Poll IPv4 directly: Docker publishes on 127.0.0.1, and "localhost" may resolve to IPv6 first.
health_url="http://127.0.0.1:${port:-8000}/api/health/ready"

echo "Building and starting the containers (the first time takes a few minutes)..."
docker compose up -d --build \
  || fail "Docker could not start the containers. See the messages above. If the port is in use, change VERIMIND_PORT in .env."

printf "Waiting for VeriMind to be ready"
for _ in $(seq 1 90); do
  if curl -fsS "$health_url" >/dev/null 2>&1; then
    printf '\n\n\033[32mVeriMind is running at %s\033[0m\n' "$url"
    echo "To stop it later, run ./stop.sh (your data is kept)."
    open_browser "$url" || echo "Open $url in your browser."
    exit 0
  fi
  printf "."
  sleep 2
done

echo
echo "VeriMind is taking longer than expected. Running a self-check:"
docker compose exec app verimind doctor || true
fail "It did not become ready in time. The self-check above should say what is wrong."
