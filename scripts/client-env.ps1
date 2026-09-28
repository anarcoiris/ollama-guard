#Requires -Version 5.1
<#
.SYNOPSIS
  Print Ollama env vars for a consumer repo (Guard agent API + lane hints).

.EXAMPLE
  .\client-env.ps1 -Repo C:\Users\soyko\Documents\Editorial_Anarcoiris
#>
param(
    [string]$Repo = "",
    [string]$GuardUrl = "http://localhost:11800"
)

$GuardV1 = "$GuardUrl/v1"

Write-Host "# Ollama Guard client environment" -ForegroundColor Cyan
Write-Host "# Paste into .env or shell session"
Write-Host ""
Write-Host "OLLAMA_GUARD_URL=$GuardUrl"
Write-Host "OLLAMA_AGENT_BASE_URL=$GuardV1"
Write-Host "OLLAMA_PLANNER_BASE_URL=$GuardV1"
Write-Host "OLLAMA_CODER_BASE_URL=$GuardV1"
Write-Host "OLLAMA_CHAT_BASE_URL=$GuardV1"
Write-Host "OLLAMA_BASE_URL=$GuardV1"
Write-Host ""
Write-Host "# Models (Guard routes by model name on :11800)"
Write-Host "OLLAMA_PLANNER_MODEL=qwen3:4b-thinking-2507-q4_K_M"
Write-Host "OLLAMA_MODEL=coder-critic"
Write-Host "OLLAMA_REVIEW_MODEL=chat-analyzer-92k"
Write-Host "OLLAMA_EXPORT_MODEL=coder-critic"
Write-Host ""
Write-Host "# Optional lane headers when model alone is ambiguous"
Write-Host "# X-Ollama-Lane: planner | chat | coder | paracoder"
Write-Host "# X-Guard-Client: editorial"
Write-Host ""
Write-Host "# Lane mirror URLs (legacy / explicit container callers)"
Write-Host "OLLAMA_PLANNER_URL=http://localhost:11434"
Write-Host "OLLAMA_CHAT_URL=http://localhost:11435"
Write-Host "OLLAMA_CODER_URL=http://localhost:11436"
Write-Host "OLLAMA_PARACODER_URL=http://localhost:11437"

if ($Repo) {
    Write-Host ""
    Write-Host "# Repo: $Repo" -ForegroundColor DarkGray
    $name = Split-Path -Leaf $Repo
    if ($name -match "Editorial") {
        Write-Host "# Editorial: set OLLAMA_CODER_BASE_URL and OLLAMA_PLANNER_BASE_URL to Guard; use OLLAMA_REVIEW_MODEL for intake"
    }
    if ($name -match "pwsh_agent") {
        Write-Host "# pwsh_agent: point config.yaml endpoints to Guard or lane mirrors"
    }
}
