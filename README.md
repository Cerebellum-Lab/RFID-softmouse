# RFID-softmouse

Local + optional Postgres mirror for SoftMouse colony data keyed by RFID, with FastAPI services for rapid metadata lookups in acquisition workflows.

## Current Capabilities (SQLite Path)
* SQLite mirror schema (`db.py`) with mice + genotypes
* CSV ETL loader (`etl_softmouse.py`)
* FastAPI service (`fastapi_service.py`) for `/mouse/{rfid}` queries
* GUI integration (RFID lookup) in acquisition script (not detailed here)

## Advanced: Postgres Mirror
If you need multi-user concurrency, richer relational queries, or write-back staging, use the Postgres components.

### Components
| File | Purpose |
|------|---------|
| `pg_schema.sql` | Core tables + materialized view `mouse_full` |
| `pg_init.py` | Apply schema & refresh materialized view |
| `pg_etl.py` | Load exported CSVs into Postgres, refresh view |
| `pg_api.py` | FastAPI API over Postgres mirror |
| `writeback_queue.py` | Append-only queue for desired mutations |
| `apply_patches_job.py` | Stub processor for queued patches |

### Quick Start (Postgres)
```powershell
# 1. Set DSN (example)
$env:PG_DSN = "postgresql://postgres:postgres@localhost:5432/softmouse"

# 2. Apply schema
python .\pg_init.py

# 3. Place exports
#   .\exports\mice.csv
#   .\exports\genotypes.csv
#   .\exports\cages.csv
#   .\exports\matings.csv
#   .\exports\litters.csv

# 4. Run ETL
python .\pg_etl.py --exports .\exports

# 5. Start API
uvicorn pg_api:app --host 127.0.0.1 --port 8090 --reload

# 6. Query
curl http://127.0.0.1:8090/mouse/ABC123
```

### Materialized View
`mouse_full` flattens per-RFID data (genotypes, cage history) for ultra-fast single-row lookups. ETL refreshes it with `REFRESH MATERIALIZED VIEW CONCURRENTLY` to avoid blocking readers.

### Write-Back Queue Concept
```powershell
# Enqueue a cage change
python .\writeback_queue.py enqueue --op update_mouse --rfid ABC123 --change cage_id=C-120

# List queued patches
python .\writeback_queue.py list

# Process (stub prints)
python .\apply_patches_job.py
```
Future: Implement Playwright automation: apply forms or batch import, then mark queue entries `status=applied` or `status=error`.

### Choosing a Path
| Scenario | Use |
|----------|-----|
| Single rig, offline tolerant | SQLite only |
| Multiple rigs, shared colony state | Postgres mirror |
| Need audited edits / write-back | Postgres + queue |

### Refresh Scheduling
Run `pg_etl.py` hourly via Windows Task Scheduler or cron (WSL / server). For most colonies, hourly is sufficient.

### License / Attribution
Internal tooling scaffold. Add license details here if distributing.

---
Generated scaffolding includes placeholders; extend with authentication, logging, and error handling before production use.

## Authentication (Token Placeholder)
Both APIs (`fastapi_service.py` and `pg_api.py`) now support optional token protection using `auth_placeholder.py`.

Setup:
1. Create a file `auth_tokens.txt` with one token per line (e.g. `devtoken123`).
2. Or set environment variable `AUTH_TOKEN` to a single token.
3. Start the service. Endpoints (except `/health`) require header:
	`Authorization: Bearer devtoken123`

Example:
```powershell
curl -H "Authorization: Bearer devtoken123" http://127.0.0.1:8090/mouse/ABC123
```

## Extended Postgres API Endpoints
| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/health` | GET | no | Service / DB status |
| `/mouse/{rfid}` | GET | yes | Single mouse record from `mouse_full` |
| `/refresh` | POST | yes | Manually refresh `mouse_full` materialized view |
| `/queue` | GET | yes | List queued patches (optional `?status=pending`) |
| `/queue/{rfid}` | GET | yes | List patches for a specific RFID |

SQLite API (`fastapi_service.py`):
| Endpoint | Method | Auth |
|----------|--------|------|
| `/health` | GET | no |
| `/mouse/{rfid}` | GET | yes |
| `/reload` | POST | yes |
| `/` | GET | yes |

## Manual Refresh vs ETL
`/refresh` only re-materializes the view; it does not import new data. Use ETL first, then refresh (ETL script already refreshes automatically). Use `/refresh` if you alter underlying tables outside ETL.

## Queue Status Lifecycle
`writeback_queue.py` entries transition: `pending -> processing -> done|error`.
`apply_patches_job.py run` (when implemented) should set `processing` during work; current stub sets `done` directly.

## Windows Task Scheduler XML Samples
Import `task_hourly_etl.xml` for hourly ETL and `task_patch_job.xml` for periodic patch attempts. Edit the `<StartBoundary>` and credentials to match your environment.

## Playwright Automation Skeleton
`softmouse_playwright.py` holds login placeholder. After installing:
```powershell
pip install playwright
playwright install
python .\softmouse_playwright.py --login-only --headful
```
Replace CSS selectors and add navigation/upload steps before enabling production use.