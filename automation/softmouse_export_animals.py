#!/usr/bin/env python
# Moved from project root to automation/ for clearer organization.

from __future__ import annotations
import asyncio, argparse, os, sys, re, tempfile, shutil, time, pathlib, getpass, datetime, mimetypes, json, struct, math, hashlib
from typing import Optional, Tuple

# --- Restructuring compatibility bootstrap ---
# Ensure repository root (parent of this automation/ folder) is on sys.path so we can import root modules
# like app_logging when this script is executed directly (Python sets sys.path[0] to the script directory only).
try:
    _SCRIPT_PARENT = pathlib.Path(__file__).resolve().parent.parent
    if str(_SCRIPT_PARENT) not in sys.path:
        sys.path.insert(0, str(_SCRIPT_PARENT))
except Exception:
    pass

try:
    from app_logging import get_logger  # type: ignore
except ModuleNotFoundError as e:  # pragma: no cover - defensive
    raise ModuleNotFoundError("Failed to import app_logging after path bootstrap; ensure repository root structure is intact.") from e

log = get_logger('softmouse.export')

try:
    from playwright.async_api import async_playwright, Page
except Exception:
    async_playwright = None

try:
    import pandas as pd
except ImportError:
    pd = None

COLONY_LINK_STRICT_SELECTOR = "a"
ANIMALS_NAV_SELECTOR = "li#mice a[href*='smdb/mouse/list.do']"
GO_TO_ANIMALS_SELECTOR = "#gotoBtn"
EXPORT_BUTTON_SELECTOR = "#exportMouseMenuButton"
EXPORTS_TAB_SELECTOR = "a:has-text('Exports'), a[href*='export/history' i]"
EXPORTS_TABLE_ROWS = "table tr"
POST_LOGIN_JS_CHECK = 'typeof ISH !== "undefined" && ISH.appContext && ISH.appContext.accessUserId > 0'
EXPORT_LOG_LINK_SELECTOR = "a:has-text('Export Log'), a:has-text('Export log')"

LOGIN_FORM_SELECTOR = 'form[name="loginForm"], form[action*="login.do" i]'
LOGIN_SELECTORS = {
    'username': '#inputUsernameEmail, input[name="username"], input[id*="user" i]',
    'password': '#inputPassword, input[name="password"], input[id*="pass" i]',
    'submit': '#secureLogin, a#secureLogin, a:has-text("Secure Login"), button:has-text("Login"), input[type="submit"]'
}
ERROR_INDICATORS = [
    'text=/Invalid (username|password)/i',
    'text=/Incorrect (username|password)/i',
]

class ExportError(Exception):
    pass

# Credential utilities

def _try_keyring() -> Tuple[Optional[str], Optional[str]]:
    try:
        import keyring  # type: ignore
        u = keyring.get_password('softmouse', 'username')
        if u:
            p = keyring.get_password('softmouse', u)
            return u, p
    except Exception:
        return None, None
    return None, None

def _store_keyring(user: str, pwd: str):
    try:
        import keyring  # type: ignore
        keyring.set_password('softmouse', 'username', user)
        keyring.set_password('softmouse', user, pwd)
        log.info('Stored credentials in system keyring (service=softmouse).')
    except Exception as e:
        log.warning('Failed storing credentials in keyring: %s', e)

def get_credentials(args) -> Tuple[str, str]:
    sources: list[str] = []
    user = (os.getenv('SOFTMOUSE_USER') or '').strip()
    pwd = (os.getenv('SOFTMOUSE_PASSWORD') or '').strip()
    if user and pwd:
        sources.append('env')
    elif not args.no_keyring:
        ku, kp = _try_keyring()
        if ku and kp:
            user, pwd = ku, kp
            sources.append('keyring')
    if args.prompt and (not user or not pwd):
        user = input('SoftMouse username: ').strip() or user
        pwd = getpass.getpass('SoftMouse password: ') or pwd
        sources.append('prompt')
    if not user or not pwd:
        raise SystemExit('Not authenticated and no credentials supplied. Set env vars, keyring, or use --prompt.')
    if args.store_credentials and 'keyring' not in sources and not args.no_keyring:
        _store_keyring(user, pwd)
    fp = hashlib.sha256(user.encode('utf-8')).hexdigest()[:8]
    log.info('Credential sources: %s (user fp %s)', '+'.join(sources) or 'unknown', fp)
    return user, pwd

async def _wait_for_auth(page: Page, timeout: float = 20.0):
    start = time.time()
    while time.time() - start < timeout:
        try:
            if await page.evaluate(POST_LOGIN_JS_CHECK):
                return True
        except Exception:
            pass
        await asyncio.sleep(0.5)
    raise ExportError('Authenticated context (ISH.appContext) not detected.')

async def _find_and_click_colony(page: Page, colony_name: str, timeout: float = 20.0):
    pattern = re.compile(re.escape(colony_name), re.IGNORECASE)
    start = time.time()
    while time.time() - start < timeout:
        anchors = await page.query_selector_all('a')
        for a in anchors:
            try:
                txt = (await a.inner_text()).strip()
            except Exception:
                continue
            if pattern.search(txt):
                log.info('Clicking colony link: %s', txt)
                try:
                    await a.click()
                    await asyncio.sleep(1.0)
                    return
                except Exception as e:
                    log.warning('Click failed for %s: %s', txt, e)
        await asyncio.sleep(0.5)
    raise ExportError(f'Colony link containing "{colony_name}" not found.')

async def _goto_animals(page: Page, timeout: float = 25.0):
    start = time.time()
    while time.time() - start < timeout:
        if await _selector_exists(page, ANIMALS_NAV_SELECTOR):
            await page.click(ANIMALS_NAV_SELECTOR)
            await asyncio.sleep(1.0)
            break
        if await _selector_exists(page, GO_TO_ANIMALS_SELECTOR):
            await page.click(GO_TO_ANIMALS_SELECTOR)
            await asyncio.sleep(1.0)
            break
        await asyncio.sleep(0.5)
    if not await _selector_exists(page, EXPORT_BUTTON_SELECTOR):
        try:
            await page.goto(page.url.rstrip('/') + '/smdb/mouse/list.do')
        except Exception:
            pass
        await asyncio.sleep(1.5)
        if not await _selector_exists(page, EXPORT_BUTTON_SELECTOR):
            raise ExportError('Failed to reach Animals page (export button not found).')

async def _try_login(page: Page, user: str, pwd: str, timeout: float = 25.0):
    try:
        await page.wait_for_selector(LOGIN_FORM_SELECTOR, timeout=6000)
    except Exception:
        pass
    await _fill_first(page, LOGIN_SELECTORS['username'], user)
    await _fill_first(page, LOGIN_SELECTORS['password'], pwd)
    await _click_first(page, LOGIN_SELECTORS['submit'])
    start = time.time()
    while time.time() - start < timeout:
        try:
            if await page.evaluate(POST_LOGIN_JS_CHECK):
                await asyncio.sleep(1.0)
                if not await _any_selector_exists(page, LOGIN_SELECTORS['username']):
                    log.info('Login fallback succeeded.')
                    return
        except Exception:
            pass
        for sel in ERROR_INDICATORS:
            if await _selector_exists(page, sel):
                raise ExportError(f'Credential login failed (error indicator {sel}).')
        await asyncio.sleep(0.5)
    raise ExportError('Credential login timeout.')

# Timing helpers

def _now(): return time.perf_counter()

def _fmt_dur(start): return f"{(time.perf_counter()-start):0.3f}s"

async def export_animals(args):
    if async_playwright is None:
        raise SystemExit('Playwright not installed. Install playwright and run playwright install')
    state_exists = os.path.isfile(args.state_file)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not args.headful)
        if state_exists and not args.force_login:
            context = await browser.new_context(storage_state=args.state_file, accept_downloads=True)
        else:
            context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()
        t0 = _now(); log.info('[TIMER] start session')
        await page.goto(args.base_url, wait_until='load')
        log.info('[TIMER] navigated base (%s)', _fmt_dur(t0))
        try:
            t_auth = _now()
            await _wait_for_auth(page, timeout=6.0)
            log.info('[TIMER] auth validated via storage state (%s)', _fmt_dur(t_auth))
        except Exception:
            t_login = _now()
            user, pwd = get_credentials(args)
            await _try_login(page, user, pwd)
            log.info('[TIMER] interactive login completed (%s)', _fmt_dur(t_login))
            if args.save_state:
                await context.storage_state(path=args.state_file)
                log.info('Saved new storage state to %s', args.state_file)
        t_colony = _now(); await _find_and_click_colony(page, args.colony_name); log.info('[TIMER] colony nav complete (%s)', _fmt_dur(t_colony))
        t_animals = _now(); await _goto_animals(page); log.info('[TIMER] animals page reached (%s)', _fmt_dur(t_animals))
        # --- Download phase ---
        # Original implementation attached a context.on('download') listener AFTER clicking, which can
        # miss downloads that begin synchronously (the event fires before handler registration).
        # We now first attempt Playwright's expect_download context manager (registers listener before click).
        t_export_click = _now(); export_start_wall = time.time()
        # Network response fallback container
        response_capture: dict[str, pathlib.Path | bytes | None] = {'path': None, 'body': None}
        taskid_capture: dict[str, str | None] = {'taskid': None}

        async def _capture_response(resp):  # executed in task when potential export response detected
            try:
                body = await resp.body()
            except Exception:
                return
            if not body:
                return
            # Derive filename
            headers = {k.lower(): v for k,v in resp.headers.items()}
            dispo = headers.get('content-disposition','')
            suggested = None
            if 'filename=' in dispo:
                suggested = dispo.split('filename=')[-1].strip().strip('"').split(';')[0]
            if not suggested:
                # Guess extension from content-type
                ctype = headers.get('content-type','')
                if 'sheet' in ctype: suggested = 'softmouse_export.xlsx'
                elif 'excel' in ctype: suggested = 'softmouse_export.xls'
                else: suggested = 'softmouse_export.bin'
            target = pathlib.Path(tempfile.gettempdir())/suggested
            try:
                with open(target,'wb') as fh: fh.write(body)
                response_capture['path'] = target
                response_capture['body'] = body
                log.info('Captured export via response fallback -> %s (%d bytes)', target.name, len(body))
            except Exception as e:
                log.warning('Failed writing response fallback file: %s', e)

        def _response_listener(resp):
            try:
                if response_capture['path'] is not None:
                    return
                headers = {k.lower(): v for k,v in resp.headers.items()}
                ctype = headers.get('content-type','').lower()
                dispo = headers.get('content-disposition','').lower()
                url_l = resp.url.lower()
                if args.debug_network:
                    log.info('[NET] %s | %s | dispo=%s', resp.url, ctype or '(none)', dispo or '(none)')
                # Capture taskid early from any export-related request/response URL
                if 'taskid=' in url_l and taskid_capture.get('taskid') is None:
                    try:
                        from urllib.parse import urlparse, parse_qs
                        qs = parse_qs(urlparse(resp.url).query)
                        tid = qs.get('taskid', [None])[0]
                        if tid:
                            taskid_capture['taskid'] = tid
                            log.info('Captured export taskid=%s from %s', tid, resp.url)
                    except Exception:
                        pass
                trigger = (
                    'attachment' in dispo or
                    'application/vnd.ms-excel' in ctype or
                    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' in ctype or
                    'application/octet-stream' in ctype and 'taskid=' in url_l or
                    ('downloadfile' in url_l) or ('taskid=' in url_l and 'export' in url_l)
                )
                if trigger:
                    asyncio.create_task(_capture_response(resp))
            except Exception:
                pass

        context.on('response', _response_listener)
        path_final: pathlib.Path | None = None
        expect_timeout_ms = int(args.download_wait * 1000)
        try:
            log.info('Initiating export (context-level expect_event within %ds)...', int(args.download_wait))
            async with context.expect_event('download', timeout=expect_timeout_ms) as dl_info:
                await page.click(EXPORT_BUTTON_SELECTOR)
            download = await dl_info.value
            path_final = await _materialize_download(download)
            log.info('[TIMER] export click+download (context) done (%s)', _fmt_dur(t_export_click))
        except Exception as e:
            log.warning('Primary context download wait failed (%s); falling back to event listener + polling: %s', type(e).__name__, e)
            # Fallback: click again only if needed then use legacy listener approach
            if not path_final:
                try:
                    if await _selector_exists(page, EXPORT_BUTTON_SELECTOR):
                        await page.click(EXPORT_BUTTON_SELECTOR)
                except Exception:
                    pass
            t_wait = _now(); path_final = await _wait_for_native_download(context, args); log.info('[TIMER] download complete (fallback listener) (%s)', _fmt_dur(t_wait))
        if not path_final and response_capture['path']:
            path_final = response_capture['path']  # type: ignore[assignment]
            # SUCCESS placeholder detection & direct-only modes
            if path_final and path_final.exists() and path_final.stat().st_size < 1024:
                tiny_ok = False
                try:
                    with open(path_final,'rb') as fh: raw = fh.read(32)
                    txt = raw.decode('utf-8','ignore').strip('"\'')
                    tiny_ok = (txt.upper() == 'SUCCESS')
                except Exception:
                    txt = ''
                if tiny_ok and taskid_capture['taskid']:
                    log.info('Detected SUCCESS placeholder (%d bytes). Strategy A navigation.', path_final.stat().st_size)
                    real = await _direct_taskid_download(context, args.base_url, taskid_capture['taskid'], args.download_wait)
                    if real:
                        path_final = real; response_capture['path'] = real
                        log.info('Strategy A succeeded: %s', real.name)
                    elif args.direct_only:
                        log.info('--direct-only: attempting cookie replay for taskid=%s', taskid_capture['taskid'])
                        replay = await _cookie_replay_download(context, args.base_url, taskid_capture['taskid'])
                        if replay:
                            path_final = replay; response_capture['path'] = replay
                            log.info('Cookie replay succeeded: %s', replay.name)
                        else:
                            raise ExportError('Direct-only: navigation + cookie replay failed (placeholder only).')
                    else:
                        log.warning('Strategy A navigation failed; will continue with fallbacks (not direct-only).')
                elif args.direct_only:
                    raise ExportError('Direct-only: expected SUCCESS placeholder with taskid; conditions not met.')

        if args.direct_only:
            if not path_final or path_final.stat().st_size < 1024:
                raise ExportError('Direct-only final validation failed: file missing or too small.')
            log.info('--direct-only: skipping export log + OS fallback.')
        else:
            # Attempt Export Log workflow if still nothing
            if not path_final:
                t_log = _now();
                try:
                    path_final = await _attempt_export_log_workflow(context, page, args, export_start_wall)
                    if path_final:
                        log.info('[TIMER] export log retrieval complete (%s)', _fmt_dur(t_log))
                except Exception as e:
                    log.warning('Export log workflow failed: %s', e)
            if not path_final:
                t_os = _now(); path_final = _scan_os_downloads(args, export_start_wall)
                if path_final: log.info('[TIMER] download detected via OS fallback (%s)', _fmt_dur(t_os))
            if not path_final:
                raise ExportError('No download detected within wait window (expect, listener, and OS scan failed).')
        # Attempt Export Log workflow if still nothing (site may now queue export and require manual retrieval)
        if not path_final:
            t_log = _now();
            try:
                path_final = await _attempt_export_log_workflow(context, page, args, export_start_wall)
                if path_final:
                    log.info('[TIMER] export log retrieval complete (%s)', _fmt_dur(t_log))
            except Exception as e:
                log.warning('Export log workflow failed: %s', e)
        if not path_final:
            # Final fallback: poll OS download directory (user-provided or heuristic) for newest .xls/.xlsx file since export click.
            t_os = _now();
            path_final = _scan_os_downloads(args, export_start_wall)
            if path_final:
                log.info('[TIMER] download detected via OS fallback (%s)', _fmt_dur(t_os))
        if not path_final:
            raise ExportError('No download detected within wait window (expect, listener, and OS scan failed).')
        if args.download_dir:
            os.makedirs(args.download_dir, exist_ok=True)
            dest = pathlib.Path(args.download_dir)/path_final.name
            try:
                if dest.resolve() != path_final.resolve(): shutil.copy2(path_final, dest)
                path_final = dest; log.info('Copied downloaded file to %s', dest)
            except Exception as e: log.warning('Failed copying download to %s: %s', dest, e)
            _archive_existing(args.download_dir, path_final, archive=not args.no_archive)
        try:
            size = path_final.stat().st_size
            with open(path_final,'rb') as fh: head = fh.read(64)
            sha = hashlib.sha256(head).hexdigest()[:16]
            log.info('Download diagnostics: name=%s size=%d first16=%s sha256(first64)=%s', path_final.name, size, head[:16].hex(), sha)
            if size < 1024: log.warning('Downloaded file unexpectedly small (<1KB); may indicate server-side error page.')
        except Exception as e: log.warning('Failed gathering file diagnostics: %s', e)
        if args.parse:
            try:
                t_parse = _now(); df = await _parse_to_dataframe(path_final); log.info('[TIMER] parse complete (%s)', _fmt_dur(t_parse))
                if args.output:
                    out_path = pathlib.Path(args.output); out_path.parent.mkdir(parents=True, exist_ok=True)
                    if out_path.suffix.lower() == '.parquet': df.to_parquet(out_path, index=False)
                    else: df.to_csv(out_path, index=False)
                    log.info('Saved DataFrame to %s', out_path)
                try: print(df.head(10).to_string())
                except Exception: pass
            except Exception as e: log.warning('Parsing skipped due to error: %s', e)
        else:
            log.info('Skipping parse (--parse not supplied). Raw download retained: %s', path_final)
        await browser.close()

async def _selector_exists(page: Page, selector: str) -> bool:
    try: return (await page.query_selector(selector)) is not None
    except Exception: return False

async def _any_selector_exists(page: Page, selector_group: str) -> bool:
    for sel in [s.strip() for s in selector_group.split(',') if s.strip()]:
        if await _selector_exists(page, sel): return True
    return False

async def _wait_for_native_download(context, args) -> pathlib.Path | None:
    deadline = time.time() + args.download_wait
    captured: pathlib.Path | None = None
    event_error: Exception | None = None
    import math as _math
    async def _event_listener(download):
        nonlocal captured, event_error
        try:
            suggested = download.suggested_filename
            log.info('[EVENT] download event fired: %s', suggested)
            temp_path = await download.path()
            if temp_path: p = pathlib.Path(temp_path)
            else:
                tmp = pathlib.Path(tempfile.gettempdir())/suggested
                await download.save_as(str(tmp)); p = tmp
            captured = p
        except Exception as e: event_error = e
    context.on('download', lambda d: asyncio.create_task(_event_listener(d)))
    last_log_interval = 0
    while time.time() < deadline and not captured:
        remaining = deadline - time.time(); elapsed = args.download_wait - remaining
        if elapsed - last_log_interval >= 5:
            last_log_interval = 5 * _math.floor(elapsed/5)
            log.info('Waiting for download... elapsed=%ds remaining=%ds', int(elapsed), int(remaining))
        await asyncio.sleep(1.0)
    if captured: return captured
    if event_error: log.warning('Download event error encountered: %s', event_error)
    return None

async def _materialize_download(download) -> pathlib.Path:
    """Given a Playwright Download object, persist it to a deterministic temp file and return the path.

    The .path() may return None if the artifact is not yet finalized; in that case we explicitly save it.
    """
    try:
        tmp_path = await download.path()
    except Exception:
        tmp_path = None
    suggested = getattr(download, 'suggested_filename', None) or 'softmouse_export'
    if tmp_path:
        return pathlib.Path(tmp_path)
    # Manual save
    target = pathlib.Path(tempfile.gettempdir()) / suggested
    try:
        await download.save_as(str(target))
    except Exception as e:
        raise ExportError(f'Failed saving download: {e}')
    return target

def _scan_os_downloads(args, export_start_wall: float) -> pathlib.Path | None:
    """Heuristic scan of OS download directory for a recent SoftMouse export file.

    SoftMouse exports are typically .xls, .xlsx, or .csv and appear with 'mouse' or 'export' in name.
    We look for newest matching file within the provided wait window horizon (download-wait * 1.5 seconds).
    """
    # Determine candidate directory
    user_dir = os.path.expandvars(os.path.expanduser(args.os_download_dir)) if args.os_download_dir else None
    candidates: list[pathlib.Path] = []
    if user_dir and os.path.isdir(user_dir):
        candidates.append(pathlib.Path(user_dir))
    # Common Windows / cross-platform default
    home = pathlib.Path.home()
    default_dl = home / 'Downloads'
    if default_dl.is_dir() and default_dl not in candidates:
        candidates.append(default_dl)
    if not candidates:
        log.debug('OS fallback: no candidate download directories found.')
        return None
    # Only consider files modified after export_start_wall - 2s (grace period)
    horizon = export_start_wall - 2.0
    # Relax name pattern: Many browsers assign GUID-like names first, so rely on extension + mtime only.
    patterns = None
    newest: tuple[float, pathlib.Path] | None = None
    for base in candidates:
        try:
            for f in base.iterdir():
                if not f.is_file():
                    continue
                if f.suffix.lower() not in ('.xls', '.xlsx', '.csv'):
                    continue
                try:
                    st = f.stat()
                except Exception:
                    continue
                if st.st_mtime < horizon:
                    continue
                if patterns and not patterns.search(f.name):
                    continue
                if newest is None or st.st_mtime > newest[0]:
                    newest = (st.st_mtime, f)
        except Exception as e:
            log.debug('OS fallback scan failed for %s: %s', base, e)
    if newest:
        log.info('OS fallback selected candidate file: %s', newest[1])
        return newest[1]
    log.info('OS fallback found no matching recent files.')
    return None

def _guess_extension(body: bytes, ctype: str, url: str) -> str:
    try:
        if body.startswith(b'PK\x03\x04'): return '.xlsx'
        if body.startswith(b'\xD0\xCF\x11\xE0'): return '.xls'
        # Text-like heuristic
        sample = body[:200]
        if sample and all((32 <= b <= 126) or b in (9,10,13) for b in sample):
            if b',' in sample or b'\t' in sample: return '.csv'
    except Exception:
        pass
    ctype_l = (ctype or '').lower()
    if 'sheet' in ctype_l: return '.xlsx'
    if 'excel' in ctype_l: return '.xls'
    if any(p in url.lower() for p in ('xlsx','sheet','excel')): return '.xlsx'
    return '.bin'

async def _attempt_export_log_workflow(context, page, args, export_start_wall: float) -> pathlib.Path | None:
    """Navigate to Export Log page and attempt to download the most recent export entry.

    Strategy:
      1. Look for direct link with text 'Export Log'. If not present, try adding '/export/history' variations.
      2. On history page, identify first table row containing .xls/.xlsx hyperlink.
      3. Intercept response for that hyperlink similar to network fallback.
    """
    try:
        # Step 1: navigate to log page
        log.info('Attempting Export Log fallback workflow...')
        try:
            if await page.query_selector(EXPORT_LOG_LINK_SELECTOR):
                await page.click(EXPORT_LOG_LINK_SELECTOR)
                await asyncio.sleep(2)
        except Exception:
            pass
        # If still on animals page, attempt manual URL
        if 'export' not in page.url.lower():
            base = page.url.split('/smdb/')[0]
            candidates = [
                base + '/smdb/export/history.do',
                base + '/smdb/mouse/export/history.do',
            ]
            for u in candidates:
                try:
                    await page.goto(u, wait_until='load')
                    if 'export' in page.url.lower(): break
                except Exception:
                    continue
        # Scan for table rows with links
        rows = await page.query_selector_all('table tr')
        best_link = None; best_time = None
        for r in rows:
            try:
                link = await r.query_selector('a[href*=".xls" i], a[href*=".xlsx" i]')
                if not link: continue
                href = await link.get_attribute('href')
                if not href: continue
                # Prefer first match (assuming descending order)
                best_link = link; break
            except Exception:
                continue
        if not best_link:
            return None
        # Use expect_download again as log workflow might trigger standard download
        try:
            async with page.expect_download(timeout=int(args.download_wait*1000/2)) as dlinfo:
                await best_link.click()
            dl = await dlinfo.value
            return await _materialize_download(dl)
        except Exception:
            # Fall back to network capture of link navigation
            try:
                await best_link.click()
                await asyncio.sleep(4)
            except Exception:
                return None
            # Try scanning OS downloads quickly
            return _scan_os_downloads(args, export_start_wall)
    except Exception as e:
        log.debug('Export log workflow internal error: %s', e)
    return None

async def _direct_taskid_download(context, base_url: str, taskid: str, wait_seconds: float) -> pathlib.Path | None:
    """Attempt to force a real browser download via top-level navigation using the known taskid.

    We open a temporary page and navigate directly to the downLoadFile endpoint. If Playwright emits a
    download event we materialize it; otherwise we attempt to capture via network listener fallback.
    """
    if not taskid:
        return None
    # Normalize base
    if base_url.endswith('/'):
        base = base_url.rstrip('/')
    else:
        base = base_url
    url = f"{base}/export/downLoadFile?taskid={taskid}"
    log.info('Strategy A: direct navigation to %s', url)
    page = await context.new_page()
    timeout_ms = int(min(wait_seconds, 30) * 1000)
    try:
        async with context.expect_event('download', timeout=timeout_ms) as dl_info:
            await page.goto(url, wait_until='domcontentloaded')
        download = await dl_info.value
        real_path = await _materialize_download(download)
        await page.close()
        return real_path
    except Exception as e:
        log.warning('Strategy A navigation did not yield download event: %s', e)
        try:
            await page.close()
        except Exception:
            pass
    return None

async def _cookie_replay_download(context, base_url: str, taskid: str) -> pathlib.Path | None:
    """Replay request using session cookies via requests to fetch the real binary.

    Returns a path or None. Only used in --direct-only mode when navigation did not yield a download.
    """
    try:
        import requests  # rely on existing dependency (already in requirements)
    except Exception as e:
        log.warning('Cookie replay unavailable (requests missing): %s', e); return None
    if not taskid:
        return None
    base = base_url.rstrip('/')
    url = f"{base}/export/downLoadFile?taskid={taskid}"
    # Build cookie header from context storage state
    try:
        state = await context.storage_state()
        cookies = state.get('cookies', []) if isinstance(state, dict) else []
        cookie_header = '; '.join(f"{c['name']}={c['value']}" for c in cookies if 'name' in c and 'value' in c)
    except Exception as e:
        log.warning('Failed extracting cookies for replay: %s', e); return None
    log.info('Cookie replay GET %s', url)
    try:
        resp = requests.get(url, headers={'Cookie': cookie_header, 'Accept': '*/*'}, timeout=60)
    except Exception as e:
        log.warning('Cookie replay request failed: %s', e); return None
    if resp.status_code != 200:
        log.warning('Cookie replay non-200 status: %s', resp.status_code); return None
    data = resp.content
    if len(data) < 1024 and data.strip().decode('utf-8','ignore').upper().strip('\"\'') == 'SUCCESS':
        log.warning('Cookie replay still received SUCCESS placeholder (not real file).'); return None
    # Determine filename from headers
    dispo = resp.headers.get('Content-Disposition','')
    name = None
    if 'filename' in dispo:
        part = dispo.split('filename')[-1]
        for sep in ('*=utf-8''','="','=',):
            if sep in part:
                name = part.split(sep,1)[-1].strip().strip('"').split(';')[0]
                break
    if not name:
        name = f'softmouse_export_{taskid}.xlsx'
    target = pathlib.Path(tempfile.gettempdir())/name
    try:
        with open(target,'wb') as fh: fh.write(data)
        log.info('Cookie replay wrote %s (%d bytes)', target.name, len(data))
        return target
    except Exception as e:
        log.warning('Cookie replay write failed: %s', e)
    return None

async def _parse_to_dataframe(path: pathlib.Path):
    if pd is None: raise ExportError('pandas not installed; cannot parse export.')
    suffix = path.suffix.lower()
    if suffix in ('.xls', '.xlsx'): df = pd.read_excel(path)
    elif suffix == '.csv':
        for enc in ('utf-8','latin-1'):
            try: df = pd.read_csv(path, encoding=enc); break
            except Exception: continue
        else: raise ExportError('Failed reading CSV with utf-8/latin-1.')
    else: raise ExportError(f'Unsupported file extension: {suffix}')
    log.info('DataFrame shape: %s', df.shape); return df

async def _fill_first(page: Page, selector_group: str, value: str):
    for sel in [s.strip() for s in selector_group.split(',') if s.strip()]:
        try:
            if await page.query_selector(sel): await page.fill(sel, value); return
        except Exception: continue
    raise ExportError(f'Unable to locate input for selectors: {selector_group}')

async def _click_first(page: Page, selector_group: str):
    for sel in [s.strip() for s in selector_group.split(',') if s.strip()]:
        try:
            if await page.query_selector(sel): await page.click(sel); return
        except Exception: continue
    raise ExportError(f'Unable to locate clickable element for selectors: {selector_group}')

def _archive_existing(download_dir: str, new_file: pathlib.Path, archive: bool=True):
    if not archive: return
    arch_dir = pathlib.Path(download_dir)/'OldVersions'; arch_dir.mkdir(exist_ok=True)
    for f in pathlib.Path(download_dir).glob('*'):
        if f.is_file() and f != new_file and f.name != 'OldVersions':
            try:
                ts = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%S')
                dest = arch_dir / f"{f.stem}_{ts}{f.suffix}"; shutil.move(str(f), dest)
                log.info('Archived prior export %s -> %s', f.name, dest.name)
            except Exception as e: log.warning('Failed archiving %s: %s', f, e)

def parse_cli(argv=None):
    ap = argparse.ArgumentParser(description='Export SoftMouse Animals list (raw download or parse).')
    ap.add_argument('--state-file', default='softmouse_storage_state.json', help='Existing storage state JSON (from login script).')
    ap.add_argument('--base-url', default='https://www.softmouse.net', help='Base URL (default https://www.softmouse.net)')
    ap.add_argument('--colony-name', required=True, help='Substring of colony link text to click (e.g. "jason christie")')
    ap.add_argument('--download-dir', default='downloads_animals', help='Directory to copy/retain downloaded file (optional)')
    ap.add_argument('--output', help='Path to write parsed DataFrame (.csv or .parquet) when --parse used')
    ap.add_argument('--parse', action='store_true', help='Parse downloaded file into DataFrame (otherwise keep raw)')
    ap.add_argument('--download-wait', type=float, default=60.0, help='Seconds to wait for native browser download (default 60)')
    ap.add_argument('--headful', action='store_true', help='Run headed browser for debugging')
    ap.add_argument('--prompt', action='store_true', help='Prompt for credentials even if env/keyring present')
    ap.add_argument('--no-keyring', action='store_true', help='Disable keyring lookup/storage for this run')
    ap.add_argument('--store-credentials', action='store_true', help='Store retrieved (env/prompt) credentials into keyring')
    ap.add_argument('--force-login', action='store_true', help='Ignore stored state and perform fresh login')
    ap.add_argument('--save-state', action='store_true', help='After successful login save/overwrite --state-file')
    ap.add_argument('--debug-export', action='store_true', help='Dump page HTML if export download not captured (legacy)')
    ap.add_argument('--export-timeout', type=float, default=30.0, help='Legacy timeout for history workflow (unused unless fallback)')
    ap.add_argument('--trace-export', action='store_true', help='Record verbose network response metadata (legacy)')
    ap.add_argument('--export-log-dir', default='export_logs', help='Directory for trace logs (legacy)')
    ap.add_argument('--use-exports-tab', action='store_true', help='Force old Exports history workflow (fallback mode)')
    ap.add_argument('--no-archive', action='store_true', help='Do not archive prior exports')
    ap.add_argument('--os-download-dir', help='Absolute path to system/OS download directory to poll as last-resort (e.g. %USERPROFILE%/Downloads)')
    ap.add_argument('--debug-network', action='store_true', help='Log network responses during export window for troubleshooting')
    ap.add_argument('--direct-only', action='store_true', help='Fast path: expect SUCCESS placeholder + taskid then perform direct navigation and cookie replay. Skip other fallbacks.')
    return ap.parse_args(argv)

def main(argv=None):
    args = parse_cli(argv)
    asyncio.run(export_animals(args))

if __name__ == '__main__':
    main()
