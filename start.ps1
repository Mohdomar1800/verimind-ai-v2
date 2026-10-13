# Start VeriMind on Windows. Double-click start.bat (which runs this script), or run: .\start.ps1
# -NoBrowser skips opening the browser (useful for automation).
param([switch]$NoBrowser)
# Docker prints progress on stderr; exit codes are checked explicitly, so don't treat stderr as fatal.
$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

function Fail([string]$message) {
    Write-Host ""
    Write-Host "VeriMind could not start: $message" -ForegroundColor Red
    Write-Host ""
    try { Read-Host "Press Enter to close" | Out-Null } catch { }  # no prompt when run non-interactively
    exit 1
}

Write-Host "Starting VeriMind..." -ForegroundColor Cyan

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Docker is not installed. Install Docker Desktop from https://www.docker.com/products/docker-desktop/ and run this again."
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Fail "Docker is installed but not running. Open Docker Desktop, wait until it says it is running, then run this again."
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env with default settings."
}

$port = "8000"
$portLine = Select-String -Path ".env" -Pattern "^\s*VERIMIND_PORT\s*=\s*(\d+)" | Select-Object -First 1
if ($portLine) { $port = $portLine.Matches[0].Groups[1].Value }
$url = "http://localhost:$port"
# Poll IPv4 directly: Docker publishes on 127.0.0.1, and Windows may resolve "localhost" to IPv6 first.
$healthUrl = "http://127.0.0.1:$port/api/health/ready"

Write-Host "Building and starting the containers (the first time takes a few minutes)..."
docker compose up -d --build
if ($LASTEXITCODE -ne 0) {
    Fail "Docker could not start the containers. See the messages above. If port $port is in use, change VERIMIND_PORT in .env."
}

Write-Host "Waiting for VeriMind to be ready" -NoNewline
$ready = $false
for ($i = 0; $i -lt 90; $i++) {
    try {
        $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
    Write-Host "." -NoNewline
    Start-Sleep -Seconds 2
}
Write-Host ""

if (-not $ready) {
    Write-Host "VeriMind is taking longer than expected. Running a self-check:" -ForegroundColor Yellow
    docker compose exec app verimind doctor
    Fail "It did not become ready in time. The self-check above should say what is wrong."
}

Write-Host ""
Write-Host "VeriMind is running at $url" -ForegroundColor Green
Write-Host "To stop it later, double-click stop.bat (your data is kept)."
if (-not $NoBrowser) {
    try { Start-Process $url -ErrorAction Stop } catch { Write-Host "Open $url in your browser." }
}
