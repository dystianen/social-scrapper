# src/core/browser_manager.py
"""Manajemen browser pool dengan Playwright."""

import random
import asyncio
import sys
import json
from pathlib import Path
from typing import Optional
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from rich.console import Console

console = Console()

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1280, "height": 720},
    {"width": 1600, "height": 900},
]

STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined
    });

    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            const plugins = [
                { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                { name: 'Native Client', filename: 'internal-nacl-plugin' },
            ];
            plugins.length = 3;
            return plugins;
        }
    });

    Object.defineProperty(navigator, 'languages', {
        get: () => ['id-ID', 'id', 'en-US', 'en']
    });

    if (!window.chrome) {
        window.chrome = {};
    }
    if (!window.chrome.runtime) {
        window.chrome.runtime = {};
    }

    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => {
        if (parameters.name === 'notifications') {
            return Promise.resolve({ state: Notification.permission });
        }
        return originalQuery(parameters);
    };

    delete navigator.__proto__.webdriver;
"""


class BrowserManager:
    def __init__(self, config: dict):
        self.config = config
        self.headless = bool(config.get("headless", False))
        self.max_instances = config.get("max_instances", 3)
        self.default_timeout = config.get("default_timeout", 30000)
        self.playwright = None
        self._active_contexts: list[BrowserContext] = []
        self._semaphore = asyncio.Semaphore(self.max_instances)
        self.cookies_dir = Path(config.get("cookies_dir", "./data/cookies"))
        self.cookies_dir.mkdir(parents=True, exist_ok=True)
        self.is_windows = sys.platform == "win32"

    async def initialize(self):
        self.playwright = await async_playwright().start()

        mode_str = "[green]VISIBLE[/green]" if not self.headless else "[yellow]HEADLESS[/yellow]"
        console.print(f"[green]✓ Browser manager initialized[/green] (mode: {mode_str}, platform: {sys.platform})")

    async def shutdown(self):
        for ctx in self._active_contexts:
            try:
                await ctx.close()
            except Exception:
                pass
        if self.playwright:
            await self.playwright.stop()
        console.print("[yellow]✓ Browser manager shutdown[/yellow]")

    async def create_session(
        self,
        proxy: Optional[dict] = None,
        cookies_file: Optional[str] = None,
        extra_stealth: bool = True,
    ) -> Page:
        await self._semaphore.acquire()

        try:
            viewport = random.choice(VIEWPORTS)
            user_agent = random.choice(USER_AGENTS)

            # ── Build launch args — beda untuk Windows dan Linux ──
            args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                f"--window-size={viewport['width']},{viewport['height']}",
            ]

            # Flag Linux-only: JANGAN dipakai di Windows
            if not self.is_windows:
                args.extend([
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                ])

            # ── JANGAN pakai --disable-gpu di Windows!
            #    Flag ini mencegah browser window dari rendering ──

            launch_args = {
                "headless": self.headless,
                "args": args,
            }

            # Proxy
            if proxy:
                launch_args["proxy"] = {"server": proxy["server"]}
                if proxy.get("username"):
                    launch_args["proxy"]["username"] = proxy["username"]
                    launch_args["proxy"]["password"] = proxy.get("password", "")

            browser = await self.playwright.chromium.launch(**launch_args)

            context = await browser.new_context(
                viewport=viewport,
                user_agent=user_agent,
                locale="id-ID",
                timezone_id="Asia/Jakarta",
                java_script_enabled=True,
                bypass_csp=True,
            )

            context.set_default_timeout(self.default_timeout)

            if extra_stealth:
                await context.add_init_script(STEALTH_SCRIPT)

            # Load cookies
            if cookies_file:
                cookies_path = self.cookies_dir / cookies_file
                if cookies_path.exists():
                    try:
                        cookies = json.loads(cookies_path.read_text(encoding="utf-8"))
                        await context.add_cookies(cookies)
                        console.print(f"  [dim]Loaded cookies from {cookies_file}[/dim]")
                    except Exception as e:
                        console.print(f"  [yellow]Warning: gagal load cookies: {e}[/yellow]")

            page = await context.new_page()
            self._active_contexts.append(context)

            return page

        except Exception as e:
            self._semaphore.release()
            raise RuntimeError(f"Gagal membuat browser session: {e}")

    async def close_session(self, page: Page, save_cookies_as: Optional[str] = None):
        try:
            if save_cookies_as:
                context = page.context
                try:
                    cookies = await context.cookies()
                    if cookies:
                        path = self.cookies_dir / save_cookies_as
                        path.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
                        console.print(f"  [dim]Cookies saved to {save_cookies_as}[/dim]")
                except Exception as e:
                    console.print(f"  [yellow]Warning: gagal save cookies: {e}[/yellow]")

            context = page.context
            await context.close()
            if context in self._active_contexts:
                self._active_contexts.remove(context)
        except Exception as e:
            console.print(f"  [yellow]Warning saat close session: {e}[/yellow]")
        finally:
            self._semaphore.release()

    @property
    def active_sessions(self) -> int:
        return len(self._active_contexts)