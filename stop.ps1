# Stop VeriMind on Windows. Your runs and settings are kept; start.bat brings everything back.
Set-Location -Path $PSScriptRoot
docker compose down
if ($LASTEXITCODE -eq 0) {
    Write-Host "VeriMind stopped. Your data is kept." -ForegroundColor Green
} else {
    Write-Host "Could not stop VeriMind. Is Docker Desktop running?" -ForegroundColor Red
}
try { Read-Host "Press Enter to close" | Out-Null } catch { }
