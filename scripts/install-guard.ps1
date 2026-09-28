#Requires -Version 5.1
<#
.SYNOPSIS
  Install and start Ollama Guard in front of the multi-GPU or single-container stack.

.EXAMPLE
  .\install-guard.ps1 -Mode multi-gpu
  .\install-guard.ps1 -Mode single -Build
#>
param(
    [ValidateSet("multi-gpu", "single", "custom")]
    [string]$Mode = "multi-gpu",
    [switch]$Build,
    [switch]$LaneMirrorsOnly,
    [string]$SchemaPath = "",
    [string]$DockerDir = ""
)

$ErrorActionPreference = "Stop"
$GuardRoot = Split-Path -Parent $PSScriptRoot
if (-not $DockerDir) {
    $DockerDir = Join-Path (Split-Path -Parent $GuardRoot) "docker"
}

$composeFile = if ($Mode -eq "single") {
    Join-Path $DockerDir "docker-compose_single.yml"
} else {
    Join-Path $DockerDir "docker-compose.multi-gpu.yml"
}

$guardCompose = Join-Path $GuardRoot "docker-compose.guard.yml"

Write-Host "=== Ollama Guard install ===" -ForegroundColor Cyan
Write-Host "Mode:       $Mode"
Write-Host "Ollama:     $composeFile"
Write-Host "Guard:      $guardCompose"

if (-not (Test-Path $composeFile)) {
    throw "Compose file not found: $composeFile"
}

Push-Location $GuardRoot
try {
    $env:GUARD_BACKENDS_MODE = "docker"
    if ($LaneMirrorsOnly) {
        $env:GUARD_LANE_MIRRORS = "0"
    }
    if ($Build) {
        docker compose -f $guardCompose build
    }
    Write-Host "Starting Ollama backends..." -ForegroundColor Yellow
    docker compose -f $composeFile up -d
    Write-Host "Starting Ollama Guard..." -ForegroundColor Yellow
    docker compose -f $composeFile -f $guardCompose up -d --build ollama-guard
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Guard agent API:  http://localhost:11800" -ForegroundColor Green
Write-Host "Lane mirrors:     http://localhost:11434-11437 (when GUARD_LANE_MIRRORS=1)" -ForegroundColor Green
Write-Host "Status:           curl http://localhost:11800/status" -ForegroundColor Green
Write-Host "Sleep ritual:     .\scripts\sleep-stack.ps1" -ForegroundColor Green
