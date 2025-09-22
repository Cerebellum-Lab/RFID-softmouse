#!/usr/bin/env python
# Moved from project root to automation/ for clearer organization.

from __future__ import annotations
import asyncio, argparse, os, sys, re, tempfile, shutil, time, pathlib, getpass, datetime, mimetypes, json, struct, math, hashlib
from typing import Optional, Tuple
from app_logging import get_logger

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
        t_export_click = _now();
        try:
            await page.click(EXPORT_BUTTON_SELECTOR)
            log.info('Clicked export button; waiting up to %ds for native download to appear in browser download dir.', int(args.download_wait))
        except Exception as e:
            raise ExportError(f'Failed to click export button: {e}')
        log.info('[TIMER] export click done (%s)', _fmt_dur(t_export_click))
        t_wait = _now(); path_final = await _wait_for_native_download(context, args); log.info('[TIMER] download complete (%s)', _fmt_dur(t_wait))
        if not path_final: raise ExportError('No download detected within wait window.')
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
    return ap.parse_args(argv)

def main(argv=None):
    args = parse_cli(argv)
    asyncio.run(export_animals(args))

if __name__ == '__main__':
    main()
