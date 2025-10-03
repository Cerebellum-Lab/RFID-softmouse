#!/usr/bin/env python
# Moved from project root to automation/ for clearer organization.

from __future__ import annotations
import asyncio, argparse, os, sys, re, tempfile, shutil, time, pathlib, getpass, datetime, hashlib
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
            # Defer saving state until animals page is reached (so future fast loads land there quickly)
            save_state_deferred = bool(args.save_state)
        else:
            save_state_deferred = False
        # Fast-path option: if user requests --fast-animals and we used a stored state, jump directly.
        if args.fast_animals and state_exists and not args.force_login:
            base_root = args.base_url.rstrip('/')
            animals_url = f"{base_root}/smdb/mouse/list.do"
            try:
                t_fast = _now(); await page.goto(animals_url, wait_until='load'); log.info('[TIMER] fast animals direct nav (%s)', _fmt_dur(t_fast))
            except Exception as e:
                log.warning('Fast animals navigation failed (%s); falling back to colony workflow.', e)
                t_colony = _now(); await _find_and_click_colony(page, args.colony_name); log.info('[TIMER] colony nav complete (%s)', _fmt_dur(t_colony))
                t_animals = _now(); await _goto_animals(page); log.info('[TIMER] animals page reached (%s)', _fmt_dur(t_animals))
        else:
            t_colony = _now(); await _find_and_click_colony(page, args.colony_name); log.info('[TIMER] colony nav complete (%s)', _fmt_dur(t_colony))
            t_animals = _now(); await _goto_animals(page); log.info('[TIMER] animals page reached (%s)', _fmt_dur(t_animals))
        # Now safe to persist state (after animals page reached) if deferred
        try:
            if 'save_state_deferred' in locals() and save_state_deferred:
                await context.storage_state(path=args.state_file)
                log.info('Saved new storage state to %s (post-animals page)', args.state_file)
        except Exception as e:
            log.warning('Deferred state save failed: %s', e)
        # If running in login-only mode, exit now after successful navigation/state persistence.
        if getattr(args, 'login_only', False):
            log.info('--login-only specified: ending session after animals page verification.')
            try:
                await browser.close()
            except Exception:
                pass
            return
        # --- Simplified Strategy A only: click export -> capture taskid -> cookie replay (with optional direct navigation attempt) ---
        export_start_wall = time.time()
        taskid_capture: dict = {'taskid': None}  # type: ignore (python 3.7 compat)
        placeholder_seen: dict = {'ok': False}

        async def _inspect_response(resp):
            try:
                url_l = resp.url.lower()
                if args.debug_network:
                    headers = {k.lower(): v for k,v in resp.headers.items()}
                    ctype = headers.get('content-type','') or '(none)'
                    dispo = headers.get('content-disposition','') or '(none)'
                    log.info('[NET] %s | %s | dispo=%s', resp.url, ctype, dispo)
                if 'taskid=' in url_l and taskid_capture['taskid'] is None:
                    from urllib.parse import urlparse, parse_qs
                    qs = parse_qs(urlparse(resp.url).query)
                    tid = qs.get('taskid', [None])[0]
                    if tid:
                        taskid_capture['taskid'] = tid
                        log.info('Captured export taskid=%s', tid)
                # Detect SUCCESS placeholder body (small) on downLoadFile
                if 'downloadfile' in url_l and 'taskid=' in url_l:
                    try:
                        body = await resp.body()
                        if body and len(body) < 64 and body.strip().decode('utf-8','ignore').upper().strip('\"\'') == 'SUCCESS':
                            placeholder_seen['ok'] = True
                            log.info('Observed SUCCESS placeholder (server signaled export ready to fetch).')
                    except Exception:
                        pass
            except Exception:
                pass

        context.on('response', lambda r: asyncio.create_task(_inspect_response(r)))
        log.info('Clicking export button to initiate export job...')
        await page.click(EXPORT_BUTTON_SELECTOR)
        # Wait until we have taskid (or timeout)
        deadline = time.time() + args.download_wait
        while time.time() < deadline and taskid_capture['taskid'] is None:
            await asyncio.sleep(0.25)
        if not taskid_capture['taskid']:
            raise ExportError('Failed to capture export taskid within wait window.')
        taskid = taskid_capture['taskid']  # type: ignore
        # Optional: attempt direct navigation first (may or may not yield event). We give it 6 seconds.
        direct_path = await _direct_taskid_download(context, args.base_url, taskid, wait_seconds=6.0)
        path_final = None  # type: Optional[pathlib.Path]
        if direct_path and direct_path.stat().st_size > 1024:
            path_final = direct_path
            log.info('Direct navigation produced a file (%d bytes).', path_final.stat().st_size)
        # Cookie replay loop until non-placeholder file or timeout
        if not path_final:
            log.info('Attempting cookie replay fetch loop...')
            attempt = 0
            backoff = 1.5
            while time.time() < deadline:
                attempt += 1
                fetched = await _cookie_replay_download(context, args.base_url, taskid)
                if fetched and fetched.exists() and fetched.stat().st_size > 1024:
                    path_final = fetched
                    log.info('Cookie replay success on attempt %d (%d bytes).', attempt, fetched.stat().st_size)
                    break
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 6.0)
            if not path_final:
                raise ExportError('Cookie replay did not yield a non-placeholder file before timeout.')
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
        # Always attempt parse & show head
        try:
            t_parse = _now(); df = await _parse_to_dataframe(path_final); log.info('[TIMER] parse complete (%s)', _fmt_dur(t_parse))
            if args.output:
                out_path = pathlib.Path(args.output); out_path.parent.mkdir(parents=True, exist_ok=True)
                if out_path.suffix.lower() == '.parquet': df.to_parquet(out_path, index=False)
                else: df.to_csv(out_path, index=False)
                log.info('Saved DataFrame to %s', out_path)
            # Print first few lines including headers
            try:
                print(df.head(10).to_string(index=False))
            except Exception:
                pass
        except Exception as e:
            log.warning('Parsing skipped due to error: %s', e)
        await browser.close()

async def _selector_exists(page: Page, selector: str) -> bool:
    try: return (await page.query_selector(selector)) is not None
    except Exception: return False

async def _any_selector_exists(page: Page, selector_group: str) -> bool:
    for sel in [s.strip() for s in selector_group.split(',') if s.strip()]:
        if await _selector_exists(page, sel): return True
    return False

async def _guess_extension(*args, **kwargs):  # retained for compatibility (no longer used)
    return '.xlsx'

async def _direct_taskid_download(context, base_url: str, taskid: str, wait_seconds: float):  # -> Optional[pathlib.Path]
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
        # Attempt to use playwright temporary path
        try:
            tmp_path = await download.path()
        except Exception:
            tmp_path = None
        suggested = getattr(download, 'suggested_filename', None) or f'softmouse_export_{taskid}.xlsx'
        if tmp_path:
            real_path = pathlib.Path(tmp_path)
        else:
            target = pathlib.Path(tempfile.gettempdir())/suggested
            try:
                await download.save_as(str(target))
            except Exception:
                real_path = None
            else:
                real_path = target
        await page.close()
        return real_path
    except Exception as e:
        log.warning('Strategy A navigation did not yield download event: %s', e)
        try:
            await page.close()
        except Exception:
            pass
    return None

async def _cookie_replay_download(context, base_url: str, taskid: str):  # -> Optional[pathlib.Path]
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
    ap.add_argument('--fast-animals', action='store_true', help='With saved state: navigate directly to animals list page, skipping colony click path.')
    ap.add_argument('--output', help='Path to write parsed DataFrame (.csv or .parquet) when --parse used')
    ap.add_argument('--parse', action='store_true', help='Parse downloaded file into DataFrame (otherwise keep raw)')
    ap.add_argument('--download-wait', type=float, default=60.0, help='Seconds to wait for native browser download (default 60)')
    ap.add_argument('--headful', action='store_true', help='Run headed browser for debugging')
    ap.add_argument('--prompt', action='store_true', help='Prompt for credentials even if env/keyring present')
    ap.add_argument('--no-keyring', action='store_true', help='Disable keyring lookup/storage for this run')
    ap.add_argument('--store-credentials', action='store_true', help='Store retrieved (env/prompt) credentials into keyring')
    ap.add_argument('--force-login', action='store_true', help='Ignore stored state and perform fresh login')
    ap.add_argument('--save-state', action='store_true', help='After successful login save/overwrite --state-file')
    ap.add_argument('--no-archive', action='store_true', help='Do not archive prior exports')
    ap.add_argument('--debug-network', action='store_true', help='Log network responses during export window for troubleshooting')
    ap.add_argument('--login-only', action='store_true', help='Stop after reaching animals page (no export/download).')
    return ap.parse_args(argv)

def main(argv=None):
    args = parse_cli(argv)
    asyncio.run(export_animals(args))

if __name__ == '__main__':
    main()
