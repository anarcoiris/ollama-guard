#Requires -Version 5.1
<#
.SYNOPSIS
  Drain Guard queue, optionally recreate Ollama stack, warm pins, undrain.

.EXAMPLE
  .\sleep-stack.ps1
  .\sleep-stack.ps1 -ForceRecreate -IncludeParacoder
#>
param(
    [switch]$ForceRecreate,
    [switch]$SoftUnload,
    [switch]$IncludeParacoder,
    [int]$DrainTimeoutSec = 1800,
    [string]$GuardUrl = "http://localhost:11800",
    [string]$DockerDir = ""
)

$ErrorActionPreference = "Stop"
$GuardRoot = Split-Path -Parent $PSScriptRoot
if (-not $DockerDir) {
    $DockerDir = Join-Path (Split-Path -Parent $GuardRoot) "docker"
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logDir = Join-Path (Split-Path -Parent $GuardRoot) "logs\guard"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$snapshotPath = Join-Path $logDir "sleep-$timestamp.json"

function Invoke-GuardJson {
    param([string]$Method, [string]$Path, [object]$Body = $null)
    $uri = "$GuardUrl$Path"
    $params = @{ Method = $Method; Uri = $uri; ContentType = "application/json" }
    if ($Body) { $params.Body = ($Body | ConvertTo-Json) }
    try {
        return Invoke-RestMethod @params
    } catch {
        Write-Warning "Guard API $Method $Path failed: $_"
        return $null
    }
}

Write-Host "=== Ollama sleep ritual ===" -ForegroundColor Cyan

Write-Host "[1/6] Drain Guard (wait up to ${DrainTimeoutSec}s)..."
$drain = Invoke-GuardJson -Method POST -Path "/admin/drain" -Body @{ timeout = $DrainTimeoutSec }
if ($drain) {
    Write-Host "  in_flight drain result: drained=$($drain.drained)" -ForegroundColor $(if ($drain.drained) { "Green" } else { "Yellow" })
}

if ($SoftUnload) {
    Write-Host "[2/6] Soft unload pinned models..."
    $warmScript = Join-Path $DockerDir "warm-pinned-models.ps1"
    $pins = @(
        @{ Port = 11434; Model = "qwen3:4b-thinking-2507-q4_K_M" }
        @{ Port = 11435; Model = "chat-analyzer-92k" }
        @{ Port = 11436; Model = "coder-critic" }
    )
    foreach ($p in $pins) {
        try {
            Invoke-RestMethod -Method Post -Uri "http://localhost:$($p.Port)/api/generate" `
                -ContentType "application/json" `
                -Body (@{ model = $p.Model; keep_alive = 0 } | ConvertTo-Json) | Out-Null
            Write-Host "  Unloaded $($p.Model) on :$($p.Port)"
        } catch {
            Write-Host "  Skip unload :$($p.Port) — $_" -ForegroundColor DarkYellow
        }
    }
} else {
    Write-Host "[2/6] Skip soft unload (use -SoftUnload to enable)"
}

if ($ForceRecreate) {
    Write-Host "[3/6] docker compose --force-recreate..."
    Push-Location $DockerDir
    try {
        $services = @("ollama-planner", "ollama-chat", "ollama-coder")
        if ($IncludeParacoder) { $services += "ollama-paracoder" }
        docker compose -f docker-compose.multi-gpu.yml up -d --force-recreate @services
    } finally {
        Pop-Location
    }
} else {
    Write-Host "[3/6] Skip recreate (use -ForceRecreate for full stack recycle)"
}

Write-Host "[4/6] Warm pinned models..."
$warm = Join-Path $DockerDir "warm-pinned-models.ps1"
if (Test-Path $warm) {
    & $warm
} else {
    Write-Warning "warm-pinned-models.ps1 not found at $warm"
}

Write-Host "[5/6] Undrain Guard..."
$undrain = Invoke-GuardJson -Method POST -Path "/admin/undrain"

Write-Host "[6/6] Snapshot status..."
$status = Invoke-GuardJson -Method GET -Path "/status"
$snapshot = @{
    timestamp = $timestamp
    drain     = $drain
    undrain   = $undrain
    status    = $status
    force_recreate = [bool]$ForceRecreate
}
$snapshot | ConvertTo-Json -Depth 8 | Set-Content -Path $snapshotPath -Encoding UTF8
Write-Host "  Wrote $snapshotPath" -ForegroundColor Green
Write-Host "Done." -ForegroundColor Cyan
