# Ollama Guard

Relay and queue layer in front of multi-lane Ollama Docker stacks. One nerve center for OpenClaw, Editorial, repo-hygiene, pwsh_agent, and nanoclaw.

Stack overview: [`../README.md`](../README.md) · Docker deploy: [`../docker/DEPLOYMENT.md`](../docker/DEPLOYMENT.md)

## Features

- **Agent API** on `:11800` — route by model name or `X-Ollama-Lane` header
- **Lane mirrors** on `:11434`–`:11437` (Phase 2) — drop-in for legacy port-based clients
- **Per-lane FIFO queue** with semaphores matching `ollama_num_parallel`
- **GPU group lock** — blocks coder vs paracoder contention on shared GTX 1070 #1
- **Drain / undrain** — finish in-flight work before maintenance
- **Sleep ritual** — `scripts/sleep-stack.ps1` (drain → recreate → warm → undrain)
- **CCH normalize** — prefix-cache friendly (replaces nanoclaw `:11999` proxy)

## Quick start

```powershell
cd C:\Users\soyko\Documents\Ollama\guard
.\scripts\install-guard.ps1 -Mode multi-gpu -Build

# Status
curl http://localhost:11800/status

# Client env block
.\scripts\client-env.ps1 -Repo C:\Users\soyko\Documents\Editorial_Anarcoiris

# When VRAM feels sticky
.\scripts\sleep-stack.ps1 -ForceRecreate
```

## Architecture

| Entry | Port | Routing |
|-------|------|---------|
| Agent API | 11800 | Model in body + optional `X-Ollama-Lane` |
| Lane mirror | 11434–11437 | Fixed lane from listen port |

Config: [`../docker/models/r1c-v3.schema.yaml`](../docker/models/r1c-v3.schema.yaml)

## Admin API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness |
| GET | `/status` | Queue depth, in-flight, lanes |
| POST | `/admin/drain` | Stop accepting; wait for in-flight |
| POST | `/admin/undrain` | Resume |
| POST | `/admin/recycle` | Drain + optional host recycle script |

## Deployment modes

### Phase 1 (default)

Guard on `:11800` only. Ollama backends keep existing host ports — zero breakage.

```powershell
docker compose -f ../docker/docker-compose.multi-gpu.yml -f docker-compose.guard.yml up -d --build ollama-guard
```

### Phase 2 (lane mirrors)

1. Remove host `ports:` from `ollama-planner`, `ollama-chat`, `ollama-coder`, `ollama-paracoder` in multi-gpu compose (internal Docker network only).
2. Deploy with mirror overlay:

```powershell
docker compose -f ../docker/docker-compose.multi-gpu.yml `
  -f docker-compose.guard.yml `
  -f docker-compose.guard-mirrors.yml up -d --build
```

### Single-container legacy

```powershell
.\scripts\install-guard.ps1 -Mode single -Build
```

Uses `guard.defaults.yaml` single-lane profile when schema is not mounted.

## Client headers

```
X-Ollama-Lane: coder | chat | planner | paracoder
X-Guard-Client: editorial
```

## Development

```powershell
cd Ollama/guard
pip install -r requirements.txt
$env:GUARD_DEFAULTS = "config/guard.defaults.yaml"
$env:GUARD_BACKENDS_MODE = "host"
python -m guard.run
pytest
```

## Metrics

JSONL at `Ollama/logs/guard/metrics.jsonl` (same pattern as repo-hygiene).

## Risks

- Guard is a single point of entry — use `restart: unless-stopped` and keep debug bypass to internal ports during migration.
- Reboot only if `nvidia-smi` shows orphan VRAM after `sleep-stack.ps1 -ForceRecreate`.
