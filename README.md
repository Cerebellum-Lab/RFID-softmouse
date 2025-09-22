## RFID-softmouse (Simplified Core)

This repository has been reduced to the essential tooling used in daily workflows:

1. Automated SoftMouse export + credential handling (Playwright).
2. Real‑time multi‑camera acquisition + Arduino pellet delivery + compression.
3. Optional RFID lookup utilities (local / HTTP fallback) integrated with acquisition.

All Postgres / ETL / legacy HTML pages and auxiliary API scaffolding have been removed for clarity.

### Directory Layout
```
automation/                SoftMouse automation (login + export)
	softmouse_export_animals.py
	softmouse_playwright.py
acquisition/               Acquisition, compression, camera + Arduino control
	arduinoCtrl_v5.py
	multiCam_DLC_PySpin_v2.py
	multiCam_DLC_utils_v2.py
	multiCam_RT_videoAcquisition_v5.py
	compressVideos_v3.py
	systemdata.yaml          (moved here from project root)
rfid/                      RFID helpers (wrappers kept for future refactor)
	rfid_lookup.py
	rfid_serial_listener.py
logs/                      Central log output (app.log)
Users/                     Per‑user GUI preference YAML (ignored by git)
```

### Credential Layering (Automation Scripts)
Both `softmouse_export_animals.py` and `softmouse_playwright.py` resolve credentials in this order:
1. Environment variables: `SOFTMOUSE_USER`, `SOFTMOUSE_PASSWORD`
2. System keyring (if available; can be skipped with `--no-keyring`)
3. Interactive prompt (non‑echo for password). Optionally store with `--store-credentials`.

No username/password CLI flags are required or accepted anymore.

### SoftMouse Export Usage
Download the native `.xlsx` (no mutation) and optionally parse to CSV/JSON if desired.

```powershell
python .\automation\softmouse_export_animals.py --colony-name "Colony Name" --headful --download-wait 75 --parse
```
Key flags:
* `--headful`            Launch visible Chromium (omit for headless).
* `--download-wait N`    Seconds to wait for native download (default 60).
* `--parse`              Parse downloaded file into a Pandas DataFrame (logged only; add your own persistence if needed).
* `--browser-download-dir PATH`  Override default browser download folder discovery.
* `--download-trace`     Extra per‑phase timing logs even if not verbose.
* `--store-credentials`  Persist prompted credentials to keyring.

Timing + diagnostics logged (bytes, first hex bytes, short hash). The script exits non‑zero if the download does not appear within the timeout.

### Login Test Only
```powershell
python .\automation\softmouse_playwright.py --login-only --headful
```
Exports a reusable `softmouse_storage_state.json` for future authenticated contexts (Playwright).

### Multi‑Camera Acquisition GUI
The GUI (wxPython + Matplotlib) coordinates:
* PySpin / Spinnaker camera acquisition
* Live preview + ROI + pellet/stimulus ROI alignment
* Automated pellet delivery logic (Arduino serial)
* Optional RFID metadata linking (writes metalink entries processed post‑compression)
* Background compression to H.264 (`compressVideos_v3.py`) with metadata propagation

Launch (ensure dependencies + camera drivers installed):
```powershell
python .\acquisition\multiCam_RT_videoAcquisition_v5.py
```

`systemdata.yaml` now lives inside `acquisition/` so relative imports (e.g., `multiCam_DLC_utils_v2.read_config()`) continue to work after refactor.

### RFID Utilities
Standalone serial listener:
```powershell
python .\rfid\rfid_serial_listener.py --auto --raw
```
Lookup helper (used by GUI) attempts HTTP FastAPI first (if you later re‑introduce it), then falls back to local DB (legacy code paths retained but external DB modules were removed—adjust if needed).

### Logs
Unified logging goes to `logs/app.log`. Adjust formatting or rotation in `app_logging.py`.

### Users Directory
`Users/` stores per‑user preference YAMLs plus `prev_user.txt`. It is ignored by git (`.gitignore`) so local personalization does not create noise.

### Dependencies
See `requirements.txt`. Ensure PySpin / Spinnaker SDK and appropriate camera drivers are installed separately (not pip-installable). For Playwright:
```powershell
pip install -r requirements.txt
playwright install
```

### Minimal Example: End‑to‑End Export
```powershell
set SOFTMOUSE_USER=your_user
set SOFTMOUSE_PASSWORD=your_pass
python .\automation\softmouse_export_animals.py --colony-name "Example Colony" --headful
```

### Roadmap / Next Ideas
* Inline CLI flag to persist parsed animal table to CSV/Parquet.
* Replace remaining root import reliance in migrated acquisition modules (currently some use original names for backwards compatibility) with fully internal relative imports.
* Optional lightweight FastAPI re‑add strictly for RFID if needed.

### License
Internal research tooling. Add an explicit LICENSE file before external distribution.

---
Refactor summary: Removed ETL / DB / HTML artifacts. Focus now on robust acquisition & export workflows with clear logging and secure credential handling.