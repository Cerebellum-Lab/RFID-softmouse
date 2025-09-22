"""SoftMouse automation helper (login + future actions) (moved to automation/ folder)."""
from __future__ import annotations
import os, asyncio, argparse, getpass, sys, time, hashlib, pathlib
from typing import Tuple

# --- Restructuring compatibility bootstrap ---
# Add repository root to sys.path so imports of modules that lived at project root still work
try:
	_SCRIPT_PARENT = pathlib.Path(__file__).resolve().parent.parent
	if str(_SCRIPT_PARENT) not in sys.path:
		sys.path.insert(0, str(_SCRIPT_PARENT))
except Exception:
	pass

from app_logging import get_logger

try:
	from playwright.async_api import async_playwright
except Exception:
	async_playwright = None

log = get_logger('softmouse')

DEFAULT_CANDIDATE_URLS = [
	'https://www.softmouse.net',
	'https://softmouse.net',
	'https://app.softmouse.net',
	'https://app.softmouse.com',
]

def resolve_base_urls(cli_url: str | None) -> list[str]:
	env_url = os.getenv('SOFTMOUSE_BASE_URL', '').strip() or None
	chosen = []
	for u in [cli_url, env_url]:
		if u and u not in chosen:
			chosen.append(u.rstrip('/'))
	for u in DEFAULT_CANDIDATE_URLS:
		if u not in chosen:
			chosen.append(u)
	return chosen

LOGIN_FORM_SELECTOR = 'form[name="loginForm"], form[action*="login.do" i]'
LOGIN_SELECTORS = {
	'username': '#inputUsernameEmail, input#inputUsernameEmail, input[name="username"], input[id*="user" i], input[placeholder*="user" i], input[type="email"], input[placeholder*="Email" i]',
	'password': '#inputPassword, input#inputPassword, input[name="password"], input[id*="pass" i], input[type="password"], input[placeholder*="Pass" i]',
	'submit': '#secureLogin, a#secureLogin, a:has-text("Secure Login"), a:has-text("Secure login"), button[type="submit"], button:has-text("Login"), input[type="submit"], button:has-text("Sign In"), button:has-text("Log in")'
}
SUCCESS_INDICATORS = ['a:has-text("Logout")','text=Dashboard']
ERROR_INDICATORS = ['text=/Invalid (username|password)/i','text=/Incorrect (username|password)/i','.login-error']
POST_LOGIN_JS_CHECK = 'typeof ISH !== "undefined" && ISH.appContext && ISH.appContext.accessUserId > 0'
LOGIN_TRIGGER_SELECTORS = [
	'a:has-text("Login")','a:has-text("Log in")','a:has-text("Sign In")',
	'button:has-text("Login")','button:has-text("Log in")','button:has-text("Sign In")'
]

def load_env_file(path: str = '.env') -> None:
	if not os.path.isfile(path):
		return
	try:
		with open(path,'r',encoding='utf-8') as fh:
			for line in fh:
				line=line.strip();
				if not line or line.startswith('#'): continue
				if '=' in line:
					k,v=line.split('=',1); os.environ.setdefault(k.strip(), v.strip())
		log.debug('Loaded .env file')
	except Exception as e:
		log.warning('Failed loading .env: %s', e)

def _try_keyring():
	try:
		import keyring
		u = keyring.get_password('softmouse','username')
		if u:
			p = keyring.get_password('softmouse',u)
			return u,p
	except Exception:
		return None,None
	return None,None

def _store_keyring(user: str, pwd: str):
	try:
		import keyring
		keyring.set_password('softmouse','username',user)
		keyring.set_password('softmouse',user,pwd)
		log.info('Stored credentials in system keyring (service=softmouse).')
	except Exception as e:
		log.warning('Failed storing credentials in keyring: %s', e)

def get_credentials(prompt: bool, allow_keyring: bool=True, store_keyring: bool=False) -> Tuple[str,str]:
	sources=[]
	user=os.getenv('SOFTMOUSE_USER','').strip(); pwd=os.getenv('SOFTMOUSE_PASSWORD','').strip()
	if user and pwd: sources.append('env')
	elif allow_keyring:
		ku,kp=_try_keyring();
		if ku and kp:
			user,pwd=ku,kp; sources.append('keyring')
	if prompt and (not user or not pwd):
		user=input('SoftMouse username: ').strip() or user
		pwd=getpass.getpass('SoftMouse password: ') or pwd
		sources.append('prompt')
	if not user or not pwd:
		print('Missing credentials. Supply via env, keyring, or --prompt.', file=sys.stderr)
		raise SystemExit(2)
	if store_keyring and allow_keyring and 'keyring' not in sources:
		_store_keyring(user,pwd)
	fingerprint=hashlib.sha256(user.encode('utf-8')).hexdigest()[:8]
	log.info('Credentials sourced from %s (user fp %s)', '+'.join(sources) or 'unknown', fingerprint)
	return user,pwd

async def _selector_exists(page, selector: str) -> bool:
	try: return (await page.query_selector(selector)) is not None
	except Exception: return False

async def _any_selector_exists(page, selector_group: str) -> bool:
	for sel in [s.strip() for s in selector_group.split(',') if s.strip()]:
		if await _selector_exists(page, sel): return True
	return False

async def _fill_first(page, selector_group: str, value: str, is_password: bool=False):
	for sel in [s.strip() for s in selector_group.split(',') if s.strip()]:
		try:
			if await page.query_selector(sel): await page.fill(sel, value); return
		except Exception: continue
	raise RuntimeError(f'Unable to locate field for selector group: {selector_group}')

async def _click_first(page, selector_group: str):
	for sel in [s.strip() for s in selector_group.split(',') if s.strip()]:
		try:
			if await page.query_selector(sel): await page.click(sel); return
		except Exception: continue
	raise RuntimeError(f'Unable to locate submit control for selector group: {selector_group}')

def _dump_debug(html: str, base: str):
	fname=f'debug_login_{base.replace("https://"," ").replace("/","_").strip()}.html'.replace(' ','_')
	try:
		with open(fname,'w',encoding='utf-8') as fh: fh.write(html)
		log.info('Saved debug HTML %s', fname)
	except Exception as e: log.warning('Failed writing debug HTML: %s', e)

async def login(page, user: str, pwd: str, timeout: float, candidate_urls: list[str], debug: bool=False, screenshot: bool=False):
	last_error: Exception | None = None
	for base in candidate_urls:
		try:
			log.info('Navigating to %s', base)
			await page.goto(base, wait_until='load')
			try: await page.wait_for_selector(LOGIN_FORM_SELECTOR, timeout=5000)
			except Exception: pass
			if not await _any_selector_exists(page, LOGIN_SELECTORS['username']):
				for trig in LOGIN_TRIGGER_SELECTORS:
					try:
						if await _selector_exists(page, trig):
							log.debug('Clicking login trigger %s', trig); await page.click(trig); await asyncio.sleep(1)
							if await _any_selector_exists(page, LOGIN_SELECTORS['username']): break
					except Exception: continue
			await _fill_first(page, LOGIN_SELECTORS['username'], user)
			await _fill_first(page, LOGIN_SELECTORS['password'], pwd, is_password=True)
			await _click_first(page, LOGIN_SELECTORS['submit'])
			start=time.time()
			while time.time()-start < timeout:
				try:
					if await page.evaluate(POST_LOGIN_JS_CHECK):
						await asyncio.sleep(1.5)
						if not await _any_selector_exists(page, LOGIN_SELECTORS['username']):
							log.info('Login success on %s (JS context + form gone)', base); return
				except Exception: pass
				for sel in SUCCESS_INDICATORS:
					if await _selector_exists(page, sel):
						await asyncio.sleep(1.0)
						if not await _any_selector_exists(page, LOGIN_SELECTORS['username']):
							log.info('Login success on %s (found %s)', base, sel); return
				for sel in ERROR_INDICATORS:
					if await _selector_exists(page, sel):
						msg=f'Login error indicator {sel} on {base}'; log.error(msg); raise RuntimeError(msg)
				await asyncio.sleep(0.5)
			raise TimeoutError(f'Login timeout on {base}: success indicator not found')
		except Exception as e:
			last_error=e; log.warning('Attempt failed for %s: %s', base, e)
			if debug:
				try: html=await page.content(); _dump_debug(html, base)
				except Exception: pass
			if screenshot:
				try:
					fn=f'debug_login_{base.replace("https://"," ").replace("/","_").strip()}.png'.replace(' ','_')
					await page.screenshot(path=fn, full_page=True); log.info('Saved screenshot %s', fn)
				except Exception: pass
			try: await page.goto('about:blank')
			except Exception: pass
			continue
	raise last_error if last_error else RuntimeError('Login failed (unknown)')

async def main_async(args):
	if async_playwright is None:
		raise SystemExit('Playwright not installed. Run: pip install playwright && playwright install')
	load_env_file()
	user,pwd = get_credentials(prompt=args.prompt, allow_keyring=not args.no_keyring, store_keyring=args.store_credentials)
	candidate_urls = resolve_base_urls(args.base_url)
	async with async_playwright() as pw:
		browser = await pw.chromium.launch(headless=not args.headful)
		ctx = await browser.new_context()
		page = await ctx.new_page()
		try:
			if args.enumerate: await _enumerate_dom(page, candidate_urls[0])
			await login(page, user, pwd, args.timeout, candidate_urls, debug=args.debug, screenshot=args.screenshot)
			if args.save_state:
				state_file = args.save_state if args.save_state is not True else 'softmouse_storage_state.json'
				await ctx.storage_state(path=state_file); log.info('Saved storage state to %s', state_file)
			if args.login_only: print('Login successful.')
		except Exception as e:
			log.exception('Login failed: %s', e); print(f'Login failed: {e}', file=sys.stderr); raise SystemExit(1)
		finally:
			await browser.close()

def main(argv=None):
	ap = argparse.ArgumentParser()
	ap.add_argument('--login-only', action='store_true')
	ap.add_argument('--headful', action='store_true', help='Run non-headless for debugging')
	ap.add_argument('--prompt', action='store_true', help='Prompt for credentials even if env/keyring present')
	ap.add_argument('--no-keyring', action='store_true', help='Disable keyring lookup/storage')
	ap.add_argument('--store-credentials', action='store_true', help='Store credentials in keyring after retrieval')
	ap.add_argument('--timeout', type=float, default=20.0, help='Login timeout seconds (default 20)')
	ap.add_argument('--base-url', help='Override base URL (will try fallbacks if fails)')
	ap.add_argument('--debug', action='store_true', help='Dump page HTML on each failed attempt')
	ap.add_argument('--screenshot', action='store_true', help='Capture screenshot on each failed attempt')
	ap.add_argument('--enumerate', action='store_true', help='List candidate forms/inputs/buttons before login')
	ap.add_argument('--save-state', nargs='?', const=True, help='Save authenticated storage state to file (optional filename)')
	args = ap.parse_args(argv)
	asyncio.run(main_async(args))

async def _enumerate_dom(page, first_url: str):
	try:
		log.info('Enumerating DOM elements on %s', first_url)
		if page.url in ('about:blank',''): await page.goto(first_url, wait_until='load')
		script = """
		(() => {
		  function attrs(el){return ['id','name','type','placeholder','value','class'].reduce((o,k)=>{if(el.getAttribute(k)) o[k]=el.getAttribute(k); return o;},{});}  
		  const data={inputs:[],buttons:[],forms:[]};
		  document.querySelectorAll('form').forEach(f=>{data.forms.push({action:f.getAttribute('action'), method:f.getAttribute('method'), id:f.id, class:f.className});});
		  document.querySelectorAll('input').forEach(i=>{data.inputs.push(attrs(i));});
		  document.querySelectorAll('button').forEach(b=>{const o=attrs(b); o.text=b.innerText.trim(); data.buttons.push(o);});
		  return data;})();
		"""
		dominfo = await page.evaluate(script)
		import json, datetime
		dump_name = f'dom_enumeration_{datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")}.json'
		with open(dump_name,'w',encoding='utf-8') as fh: json.dump(dominfo, fh, indent=2)
		log.info('DOM enumeration written to %s', dump_name)
		print('\n--- FORMS ---'); [print(f) for f in dominfo.get('forms',[])]
		print('\n--- INPUTS ---'); [print({k:i.get(k) for k in ('id','name','type','placeholder') if i.get(k)}) for i in dominfo.get('inputs',[])]
		print('\n--- BUTTONS ---'); [print({k:b.get(k) for k in ('id','name','text','type') if b.get(k)}) for b in dominfo.get('buttons',[])]
		print(f'\nFull JSON saved: {dump_name}\n')
	except Exception as e: log.warning('Enumeration failed: %s', e)

if __name__ == '__main__':
	main()

