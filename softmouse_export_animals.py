"""Export Animal List automation.

Workflow:
 1. Load existing authenticated storage state (created by softmouse_playwright.py --save-state) OR perform login if state missing/invalid.
 2. Navigate to base URL (default https://www.softmouse.net)
 3. Click colony link matching --colony-name (case-insensitive substring match on anchor text)
 4. Ensure on colony page (presence of #mice or Go to Animals control)
 5. If currently on Strain tab, either click Animals nav (li#mice a[href*='smdb/mouse/list.do']) or use Go to Animals (#gotoBtn) if present
 6. On Animals page, click export button (#exportMouseMenuButton) and wait for download
 7. Parse resulting file (CSV or XLS/XLSX) into pandas DataFrame and print summary
 8. Optionally save DataFrame to --output (CSV or Parquet depending on extension)

Notes:
 - Does NOT upload anything.
 - Assumes single download triggered; clears download directory each run unless --keep-downloads.

"""
from __future__ import annotations
import asyncio, argparse, os, sys, re, tempfile, shutil, time, pathlib, getpass
from typing import Optional
from app_logging import get_logger

log = get_logger('softmouse.export')

try:
    from playwright.async_api import async_playwright, Page
except Exception as e:  # pragma: no cover
    async_playwright = None  # Allows help/--version without playwright installed

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None

COLONY_LINK_STRICT_SELECTOR = "a"  # We'll filter by inner_text regex
ANIMALS_NAV_SELECTOR = "li#mice a[href*='smdb/mouse/list.do']"
GO_TO_ANIMALS_SELECTOR = "#gotoBtn"
EXPORT_BUTTON_SELECTOR = "#exportMouseMenuButton"
POST_LOGIN_JS_CHECK = 'typeof ISH !== "undefined" && ISH.appContext && ISH.appContext.accessUserId > 0'

# Reuse refined selectors from login script (subset)
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
    """Find an anchor whose visible text contains colony_name (case-insensitive) and click it."""
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
    # Prefer explicit Animals nav
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
    # Verify we navigated (presence of export button or filter form elements typical to Animals page)
    if not await _selector_exists(page, EXPORT_BUTTON_SELECTOR):
        # Attempt direct navigation as fallback
        try:
            await page.goto(page.url.rstrip('/') + '/smdb/mouse/list.do')
        except Exception:
            pass
        await asyncio.sleep(1.5)
        if not await _selector_exists(page, EXPORT_BUTTON_SELECTOR):
            raise ExportError('Failed to reach Animals page (export button not found).')

async def _trigger_export(page: Page, download_dir: str, timeout: float = 30.0, debug: bool = False) -> pathlib.Path:
    # Ensure download directory exists & empty
    os.makedirs(download_dir, exist_ok=True)
    for f in pathlib.Path(download_dir).glob('*'):
        try:
            f.unlink()
        except Exception:
            pass
    log.info('Clicking export button %s', EXPORT_BUTTON_SELECTOR)
    try:
        async with page.expect_download() as dl_info:
            await page.click(EXPORT_BUTTON_SELECTOR)
        download = await dl_info.value
    except AttributeError:
        # Older playwright or mismatch; fallback to event listener
        await page.click(EXPORT_BUTTON_SELECTOR)
        try:
            download = await page.wait_for_event('download', timeout=timeout*1000)
        except Exception as e:
            if debug:
                try:
                    html = await page.content()
                    with open('export_debug.html','w',encoding='utf-8') as fh:
                        fh.write(html)
                    log.info('Saved export_debug.html')
                except Exception:
                    pass
            raise ExportError(f'No download event captured: {e}')
    save_path = pathlib.Path(download_dir) / download.suggested_filename
    await download.save_as(str(save_path))
    log.info('Downloaded file: %s', save_path)
    return save_path

async def _parse_to_dataframe(path: pathlib.Path):
    if pd is None:
        raise ExportError('pandas not installed; cannot parse export.')
    suffix = path.suffix.lower()
    if suffix in ('.xls', '.xlsx'):
        df = pd.read_excel(path)
    elif suffix == '.csv':
        # Try utf-8 then fallback
        for enc in ('utf-8', 'latin-1'):
            try:
                df = pd.read_csv(path, encoding=enc)
                break
            except Exception:
                continue
        else:
            raise ExportError('Failed reading CSV with utf-8/latin-1.')
    else:
        raise ExportError(f'Unsupported file extension: {suffix}')
    log.info('DataFrame shape: %s', df.shape)
    return df

async def _try_login(page: Page, user: str, pwd: str, timeout: float = 25.0):
    # Wait for potential form
    try:
        await page.wait_for_selector(LOGIN_FORM_SELECTOR, timeout=6000)
    except Exception:
        pass
    # Fill
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
        await page.goto(args.base_url, wait_until='load')
        try:
            await _wait_for_auth(page, timeout=6.0)
        except Exception:
            if not (args.user or os.getenv('SOFTMOUSE_USER')) and not args.prompt:
                raise SystemExit('Not authenticated and no credentials supplied. Provide --user/--password or --prompt.')
            # Perform login
            user, pwd = _resolve_credentials(args)
            await _try_login(page, user, pwd)
            if args.save_state:
                await context.storage_state(path=args.state_file)
                log.info('Saved new storage state to %s', args.state_file)
        await _find_and_click_colony(page, args.colony_name)
        await _goto_animals(page)
        download_path = await _trigger_export(page, args.download_dir, debug=args.debug_export)
        df = await _parse_to_dataframe(download_path)
        # Optional save
        if args.output:
            out_path = pathlib.Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if out_path.suffix.lower() == '.parquet':
                df.to_parquet(out_path, index=False)
            else:
                df.to_csv(out_path, index=False)
            log.info('Saved DataFrame to %s', out_path)
        # Print a concise preview to stdout
        try:
            print(df.head(10).to_string())
        except Exception:
            pass
        await browser.close()

async def _selector_exists(page: Page, selector: str) -> bool:
    try:
        el = await page.query_selector(selector)
        return el is not None
    except Exception:
        return False

async def _any_selector_exists(page: Page, selector_group: str) -> bool:
    for sel in [s.strip() for s in selector_group.split(',') if s.strip()]:
        if await _selector_exists(page, sel):
            return True
    return False

async def _fill_first(page: Page, selector_group: str, value: str):
    for sel in [s.strip() for s in selector_group.split(',') if s.strip()]:
        try:
            el = await page.query_selector(sel)
            if el:
                await page.fill(sel, value)
                return
        except Exception:
            continue
    raise ExportError(f'Unable to locate input for selectors: {selector_group}')

async def _click_first(page: Page, selector_group: str):
    for sel in [s.strip() for s in selector_group.split(',') if s.strip()]:
        try:
            el = await page.query_selector(sel)
            if el:
                await page.click(sel)
                return
        except Exception:
            continue
    raise ExportError(f'Unable to locate clickable element for selectors: {selector_group}')

def _resolve_credentials(args):
    user = args.user or os.getenv('SOFTMOUSE_USER')
    pwd = args.password or os.getenv('SOFTMOUSE_PASSWORD')
    if args.prompt:
        user = input('SoftMouse username: ') or user
        pwd = getpass.getpass('SoftMouse password: ') or pwd
    if not user or not pwd:
        raise SystemExit('Missing credentials. Provide --user/--password, environment vars, or --prompt.')
    return user, pwd

def parse_cli(argv=None):
    ap = argparse.ArgumentParser(description='Export SoftMouse Animals list to DataFrame (no upload).')
    ap.add_argument('--state-file', default='softmouse_storage_state.json', help='Existing storage state JSON (from login script).')
    ap.add_argument('--base-url', default='https://www.softmouse.net', help='Base URL (default https://www.softmouse.net)')
    ap.add_argument('--colony-name', required=True, help='Substring of colony link text to click (e.g. "jason christie")')
    ap.add_argument('--download-dir', default='downloads_animals', help='Directory to save raw downloaded export file')
    ap.add_argument('--output', help='Optional path to write parsed DataFrame (.csv or .parquet)')
    ap.add_argument('--headful', action='store_true', help='Run headed browser for debugging')
    ap.add_argument('--user', help='Username (overrides env SOFTMOUSE_USER)')
    ap.add_argument('--password', help='Password (overrides env SOFTMOUSE_PASSWORD)')
    ap.add_argument('--prompt', action='store_true', help='Prompt for credentials interactively')
    ap.add_argument('--force-login', action='store_true', help='Ignore stored state and perform fresh login')
    ap.add_argument('--save-state', action='store_true', help='After successful login save/overwrite --state-file')
    ap.add_argument('--debug-export', action='store_true', help='Dump page HTML if export download not captured')
    return ap.parse_args(argv)

def main(argv=None):
    args = parse_cli(argv)
    asyncio.run(export_animals(args))

if __name__ == '__main__':
    main()
