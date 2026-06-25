# src/adapters/instagram.py
"""Instagram browser scraper — login + OTP + hashtag scraping."""

import asyncio
import random
import re
from typing import Optional
from rich.console import Console
from playwright.async_api import Page

from src.adapters.base import BaseAdapter
from src.core.models import ScrapingTask, ScrapingResult, TaskStatus
from src.core.otp_handler import OTPHandler

console = Console()


class InstagramAdapter(BaseAdapter):
    platform_name = "instagram"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.active_page = None

    # ─────────────────────────────────────────────
    # LOGIN + OTP
    # ─────────────────────────────────────────────
    async def login(self, username: str, password: str, email: str = "", close_session: bool = True) -> bool:
        """
        Login ke Instagram.
        Kalau diminta OTP, otomatis ambil dari Gmail.
        """
        console.print(f"\n[cyan]🔐 Login Instagram: {username}[/cyan]")

        # Siapkan OTP handler
        gmail_config = self.config.get("gmail", {})
        otp_handler = None
        if gmail_config.get("email") and gmail_config.get("app_password"):
            otp_handler = OTPHandler(
                gmail_email=gmail_config["email"],
                app_password=gmail_config["app_password"],
            )
        else:
            console.print("[yellow]⚠ Gmail tidak dikonfigurasi, OTP harus dimasukkan manual[/yellow]")

        # ── Cek apakah ada cookies aktif di cache ──
        cookies_file = f"instagram_{username}.json"
        cookies_path = self.browser.cookies_dir / cookies_file
        
        # Load cookies jika file cookie ada
        page = await self.browser.create_session(
            cookies_file=cookies_file if cookies_path.exists() else None
        )

        try:
            # ── Cek apakah session login dari cookies masih aktif ──
            if cookies_path.exists():
                console.print("  [dim]Memeriksa apakah session login dari cookies masih aktif...[/dim]")
                await page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
                await self.human.human_delay(2, 4)
                
                # Cek elemen navigasi/beranda yang hanya muncul jika sudah login
                is_logged_in = False
                check_selectors = [
                    '[aria-label="Beranda"]',
                    'svg[aria-label="Beranda"]',
                    '[aria-label="Home"]',
                    'svg[aria-label="Home"]',
                    '[aria-label="Search"]',
                    '[role="navigation"]',
                    'a[href="/"]',
                ]
                
                for sel in check_selectors:
                    try:
                        if await page.locator(sel).first.is_visible(timeout=1500):
                            is_logged_in = True
                            break
                    except Exception:
                        pass
                
                if is_logged_in:
                    console.print(f"[bold green]✓ Session login masih aktif (cookies valid): {username}[/bold green]")
                    # Tutup popups/dialogs yang mengganggu
                    await self._dismiss_dialogs(page)
                    
                    if not close_session:
                        self.active_page = page
                    else:
                        await self.browser.close_session(page, save_cookies_as=cookies_file)
                    return True
                else:
                    console.print("  [yellow]⚠ Session cookies kedaluwarsa atau tidak valid, melakukan login murni...[/yellow]")

            # ── Proses Login Murni ──
            # Buka halaman login
            await page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded")
            await self.human.human_delay(2, 4)

            # Handle cookie consent
            try:
                allow = page.locator('button:has-text("Allow"), button:has-text("Accept")').first
                if await allow.is_visible(timeout=3000):
                    await allow.click()
                    await self.human.human_delay(1, 2)
            except Exception:
                pass

            # Tunggu form login (mendukung input username/email Instagram atau Facebook)
            login_input_selector = 'input[name="username"], input[name="email"]'
            await page.wait_for_selector(login_input_selector, timeout=15000)

            # Ketik username/email
            username_to_type = username if username else email
            await self.human.type_text(page, login_input_selector, username_to_type)
            await self.human.human_delay(0.5, 1.0)

            # Ketik password (mendukung input password Instagram atau Facebook)
            password_selector = 'input[name="password"], input[name="pass"]'
            await self.human.type_text(page, password_selector, password)
            await self.human.human_delay(0.5, 1.5)

            # Klik login
            login_selectors = [
                '[role="button"][aria-label="Log In"]',
                '[role="button"]:has-text("Log in")',
                '[role="button"]:has-text("Login")',
                'button[type="submit"]',
            ]

            for sel in login_selectors:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        await el.click()
                        break
                except Exception:
                    continue
                
            # ── Tunggu transisi setelah login (max 30 detik) ──
            console.print("  [dim]Menunggu transisi setelah login...[/dim]")
            
            success_selectors = [
                'button:has-text("Save Info")',
                'button:has-text("Save Your Login Info")',
                'button:has-text("Not Now")',
                'button:has-text("Not now")',
                '[aria-label="Home"]',
                'svg[aria-label="Home"]',
                '[aria-label="Beranda"]',
                'svg[aria-label="Beranda"]',
                '[aria-label="Search"]',
            ]
            
            failure_selectors = [
                '#slfErrorAlert',
                '[data-testid="login-error-message"]',
            ]
            
            otp_selectors = [
                'input[name="security_code"]',
                'input[name="approvals_code"]',
                'input[aria-label*="Security code"]',
                'input[aria-label*="confirmation code"]',
                'input[aria-label*="Kode"]',
                'input[placeholder*="Kode"]',
                'input[placeholder*="code"]',
                'input[placeholder*="Code"]',
                'h2:has-text("Check your email")',
                'span:has-text("Enter the code")',
                'label:has-text("Code")',
                'label:has-text("Kode")',
                '*:has-text("Check your email")',
                '*:has-text("Periksa email Anda")',
                '*:has-text("Masukkan kode")',
            ]

            login_state = None  # "success", "otp", "failed", or "timeout"
            for _ in range(30):
                # 1. Cek OTP
                otp_needed = False
                if "auth_platform" in page.url or "codeentry" in page.url:
                    otp_needed = True
                else:
                    for sel in otp_selectors:
                        try:
                            if await page.locator(sel).first.is_visible(timeout=0):
                                otp_needed = True
                                break
                        except Exception:
                            pass
                
                if otp_needed:
                    login_state = "otp"
                    break
                
                # 2. Cek Failure
                failed = False
                for sel in failure_selectors:
                    try:
                        if await page.locator(sel).first.is_visible(timeout=0):
                            failed = True
                            break
                    except Exception:
                        pass
                
                if failed:
                    login_state = "failed"
                    break
                
                # 3. Cek Success
                success = False
                if "/accounts/login" not in page.url:
                    for sel in success_selectors:
                        try:
                            if await page.locator(sel).first.is_visible(timeout=0):
                                success = True
                                break
                        except Exception:
                            pass
                
                if success:
                    login_state = "success"
                    break
                
                # 4. Cek if URL changed to homepage or other page (fallback)
                current_url = page.url
                if "/accounts/login" not in current_url and "instagram.com" in current_url:
                    if "challenge" not in current_url and "two_factor" not in current_url:
                        await asyncio.sleep(2)
                        login_state = "success"
                        break

                await asyncio.sleep(1)

            if not login_state:
                login_state = "success" if "/accounts/login" not in page.url else "timeout"

            if login_state == "otp":
                console.print("[yellow]⚠ Instagram minta verifikasi OTP[/yellow]")

                otp_code = None

                if otp_handler:
                    # Ambil OTP otomatis dari Gmail
                    otp_code = otp_handler.get_latest_otp(
                        max_wait_seconds=90,
                        poll_interval=5,
                    )

                if not otp_code:
                    # Fallback: minta user input manual
                    from rich.prompt import Prompt
                    otp_code = Prompt.ask("[yellow]Masukkan OTP secara manual[/yellow]")

                if otp_code:
                    await self._enter_otp(page, otp_code)
                    
                    # Tunggu transisi setelah OTP
                    console.print("  [dim]Menunggu transisi setelah OTP...[/dim]")
                    login_state = None
                    for _ in range(30):
                        failed = False
                        for sel in failure_selectors:
                            try:
                                if await page.locator(sel).first.is_visible(timeout=0):
                                    failed = True
                                    break
                            except Exception:
                                pass
                        if failed:
                            login_state = "failed"
                            break
                        
                        success = False
                        if "/accounts/login" not in page.url:
                            for sel in success_selectors:
                                try:
                                    if await page.locator(sel).first.is_visible(timeout=0):
                                        success = True
                                        break
                                except Exception:
                                    pass
                        if success:
                            login_state = "success"
                            break
                            
                        current_url = page.url
                        if "/accounts/login" not in current_url and "challenge" not in current_url and "two_factor" not in current_url:
                            await asyncio.sleep(2)
                            login_state = "success"
                            break
                            
                        await asyncio.sleep(1)
                    
                    if not login_state:
                        login_state = "success" if "/accounts/login" not in page.url else "timeout"
                else:
                    console.print("[red]✗ Tidak ada OTP, login gagal[/red]")
                    return False

            # ── CEK: Login berhasil? ──
            if login_state == "failed":
                try:
                    error_el = page.locator('#slfErrorAlert, [data-testid="login-error-message"]').first
                    if await error_el.is_visible(timeout=2000):
                        error_text = await error_el.inner_text()
                        console.print(f"[red]✗ Login error: {error_text}[/red]")
                    else:
                        console.print("[red]✗ Login gagal — cek username/password[/red]")
                except Exception:
                    console.print("[red]✗ Login gagal[/red]")
                return False
            elif login_state == "timeout":
                console.print("[red]✗ Login gagal — timeout menunggu transisi halaman[/red]")
                return False

            # ── Handle dialog popups ──
            await self._dismiss_dialogs(page)

            # ── Simpan cookies ──
            if close_session:
                await self.browser.close_session(page, save_cookies_as=f"instagram_{username}.json")
            else:
                # Simpan cookies ke file tanpa menutup session
                context = page.context
                try:
                    cookies = await context.cookies()
                    if cookies:
                        import json
                        path = self.browser.cookies_dir / f"instagram_{username}.json"
                        path.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
                        console.print(f"  [dim]Cookies saved to instagram_{username}.json[/dim]")
                except Exception as e:
                    console.print(f"  [yellow]Warning: gagal save cookies: {e}[/yellow]")
                
                self.active_page = page
            
            console.print(f"[bold green]✓ Login berhasil: {username}[/bold green]")
            return True

        except Exception as e:
            console.print(f"[red]✗ Login error: {e}[/red]")
            await self.browser.close_session(page)
            return False

    async def _check_if_otp_needed(self, page: Page) -> bool:
        """Cek apakah Instagram minta OTP."""
        indicators = [
            'input[name="security_code"]',
            'input[name="approvals_code"]',
            'input[aria-label*="Security code"]',
            'input[aria-label*="confirmation code"]',
            'input[aria-label*="Kode"]',
            'input[placeholder*="Kode"]',
            'input[placeholder*="code"]',
            'input[placeholder*="Code"]',
            'h2:has-text("Check your email")',
            'span:has-text("Enter the code")',
            'label:has-text("Code")',
            'label:has-text("Kode")',
            '*:has-text("Check your email")',
            '*:has-text("Periksa email Anda")',
            '*:has-text("Masukkan kode")',
            '*:has-text("Confirm")',
        ]

        for selector in indicators:
            try:
                el = page.locator(selector).first
                if await el.is_visible(timeout=2000):
                    return True
            except Exception:
                continue

        # Cek juga dari URL
        if "challenge" in page.url or "two_factor" in page.url:
            return True

        return False

    async def _enter_otp(self, page: Page, otp_code: str):
        """Masukkan kode OTP ke form Instagram."""
        console.print(f"  [cyan]Memasukkan OTP: {otp_code}[/cyan]")

        # Cari input OTP
        otp_selectors = [
            'input[name="security_code"]',
            'input[name="approvals_code"]',
            'input[aria-label*="Security code"]',
            'input[aria-label*="confirmation code"]',
            'input[aria-label*="Kode"]',
            'input[placeholder*="Kode"]',
            'input[placeholder*="code"]',
            'input[placeholder*="Code"]',
            'input[name="email"]',                # Seringkali input name="email" di halaman verifikasi email
            'input[type="tel"]',                  # Kadang OTP pakai type tel
            'input[type="number"]',
            'input[maxlength="6"]',
        ]

        otp_input = None
        for selector in otp_selectors:
            try:
                el = page.locator(selector).first
                if await el.is_visible(timeout=3000):
                    otp_input = selector
                    break
            except Exception:
                continue

        # Fallback pencarian dengan label Code/Kode
        if not otp_input:
            for label_text in ["Code", "Kode"]:
                try:
                    el = page.get_by_label(label_text, exact=False)
                    if await el.is_visible(timeout=2000):
                        otp_input = el
                        break
                except Exception:
                    pass

        if otp_input:
            if isinstance(otp_input, str):
                await self.human.type_text(page, otp_input, otp_code)
            else:
                await otp_input.fill(otp_code)
            await self.human.human_delay(0.5, 1.0)

            # Klik tombol confirm/submit (bisa button, div role="button", dsb)
            confirm_selectors = [
                'button:has-text("Confirm")',
                'button:has-text("Submit")',
                'button:has-text("Verify")',
                'button:has-text("Konfirmasi")',
                'button:has-text("Lanjutkan")',
                'button:has-text("Continue")',
                'button:has-text("Next")',
                '[role="button"]:has-text("Confirm")',
                '[role="button"]:has-text("Submit")',
                '[role="button"]:has-text("Verify")',
                '[role="button"]:has-text("Continue")',
                '[role="button"]:has-text("Lanjutkan")',
                'button[type="submit"]',
            ]

            for sel in confirm_selectors:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click()
                        break
                except Exception:
                    continue

            await self.human.human_delay(5, 8)
            console.print("[green]  ✓ OTP dimasukkan[/green]")
        else:
            console.print("[yellow]  ⚠ Tidak menemukan input OTP, coba Enter[/yellow]")
            await page.keyboard.press("Enter")
            await self.human.human_delay(5, 8)

    async def _dismiss_dialogs(self, page: Page):
        """Tutup semua dialog popup setelah login secara berurutan."""
        dialogs = [
            'button:has-text("Not Now")',
            'button:has-text("Save Info")',
            'button:has-text("Save Your Login Info")',
            'button:has-text("Not now")',
            'button:has-text("Turn on Notifications")',
            'button:has-text("Cancel")',
            'svg[aria-label="Close"]',
            'svg[aria-label="Tutup"]',
            '[aria-label="Close"]',
            '[aria-label="Tutup"]',
        ]

        # Coba klik dialog yang muncul secara dinamis sampai tidak ada lagi yang muncul
        for attempt in range(5):
            clicked = False
            for selector in dialogs:
                try:
                    btn = page.locator(selector).first
                    if await btn.is_visible(timeout=1000):
                        await btn.click()
                        console.print(f"  [dim]Dialog dismissed (Attempt {attempt+1}): {selector}[/dim]")
                        await self.human.human_delay(1.5, 3.0)
                        clicked = True
                        break  # Mulai lagi dari awal karena DOM mungkin berubah setelah click
                except Exception:
                    continue
            if not clicked:
                # Jika tidak ada yang di-click, tunggu sebentar lalu coba check sekali lagi
                await asyncio.sleep(1)
                still_visible = False
                for selector in dialogs:
                    try:
                        if await page.locator(selector).first.is_visible(timeout=0):
                            still_visible = True
                            break
                    except Exception:
                        pass
                if not still_visible:
                    break

    # ─────────────────────────────────────────────
    # SCRAPE ROUTER
    # ─────────────────────────────────────────────
    async def scrape(self, task: ScrapingTask) -> ScrapingResult:
        handlers = {
            "comments": self._scrape_comments,
            "profile_posts": self._scrape_profile_posts,
            "profile_info": self._scrape_profile_info,
            "hashtag": self._scrape_hashtag,
        }
        handler = handlers.get(task.task_type)
        if not handler:
            raise ValueError(f"Unknown task type: {task.task_type}")
        return await handler(task)

    # ─────────────────────────────────────────────
    # HASHTAG SCRAPING: #solusiku
    # ─────────────────────────────────────────────
    
    async def _scrape_hashtag(self, task: ScrapingTask) -> ScrapingResult:
        """
        Scrape post dari halaman hashtag Instagram.
        Buka setiap post → ambil komentar.
        """
        hashtag = task.params.get("hashtag", "solusiku").replace("#", "")
        max_posts = task.params.get("max_posts", 30)
        max_comments = task.params.get("max_comments_per_post", 50)
        account = self._get_account()
        hashtag_url = f"https://www.instagram.com/explore/tags/{hashtag}/"

        console.print(f"  [cyan]🔍 Membuka #{hashtag}: {hashtag_url}[/cyan]")

        page, reuse_session = await self._get_or_create_page(account)

        try:
            # Buka halaman hashtag
            await page.goto(hashtag_url, wait_until="domcontentloaded")
            await self.human.human_delay(3, 5)

            # Cek apakah hashtag page ter-load
            page_title = await page.title()
            console.print(f"  [dim]Page: {page_title[:60]}[/dim]")

            if "Page Not Found" in page_title or "login" in page.url.lower():
                console.print("[red]✗ Hashtag tidak ditemukan atau belum login[/red]")
                return ScrapingResult(
                    task_id=task.id,
                    platform=self.platform_name,
                    data=[],
                    error="Hashtag not found or not logged in",
                )

            # ── Kumpulkan link post dari grid ──
            post_links = set()
            no_new_count = 0

            console.print(f"  [dim]Scrolling untuk mengumpulkan post links...[/dim]")

            for scroll_round in range(max_posts // 6 + 5):
                # Ambil semua link post yang ada di halaman
                new_links = await page.evaluate("""
                    () => {
                        const links = document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]');
                        return [...new Set([...links].map(a => a.href))];
                    }
                """)

                before = len(post_links)
                post_links.update(new_links)
                after = len(post_links)

                if after > before:
                    console.print(f"    [dim]Scroll {scroll_round + 1}: {after} post links (+{after - before})[/dim]")
                    no_new_count = 0
                else:
                    no_new_count += 1

                if len(post_links) >= max_posts:
                    break
                if no_new_count >= 3:
                    console.print(f"    [dim]Tidak ada post baru, stop scrolling[/dim]")
                    break

                # Scroll
                await self.human.scroll_page(page, times=1)
                await self.human.human_delay(1.5, 3.0)

            # Batasi
            all_links = list(post_links)[:max_posts]
            console.print(f"  [green]✓ {len(all_links)} post ditemukan di #{hashtag}[/green]")

            # ── Buka setiap post, ambil data + komentar ──
            all_data = []

            for i, post_url in enumerate(all_links, 1):
                console.print(f"\n  [cyan]Post {i}/{len(all_links)}: {post_url}[/cyan]")

                try:
                    post_data = await self._open_post_and_scrape(
                        page, post_url, max_comments
                    )
                    if post_data:
                        all_data.append(post_data)
                        comments_count = len(post_data.get("comments", []))
                        console.print(
                            f"    [green]✓ {comments_count} komentar diambil[/green]"
                        )
                except Exception as e:
                    console.print(f"    [red]✗ Error: {e}[/red]")

                # Jeda antar post (penting untuk hindari rate limit)
                await self.human.human_delay(3, 7)
                await self.human.simulate_idle_activity(page)

            return ScrapingResult(
                task_id=task.id,
                platform=self.platform_name,
                data=all_data,
                metadata={
                    "hashtag": hashtag,
                    "total_posts": len(all_data),
                    "total_comments": sum(len(d.get("comments", [])) for d in all_data),
                },
            )

        finally:
            await self.browser.close_session(page)

    async def _open_post_and_scrape(self, page: Page, post_url: str, max_comments: int) -> dict:
        """
        Buka satu post, ambil konten + semua komentar.
        """
        await page.goto(post_url, wait_until="domcontentloaded")
        await self.human.human_delay(2, 4)

        # Tutup popups/dialogs
        await self._dismiss_dialogs(page)

        # Baca post dulu (manusia baca dulu sebelum scroll ke komentar)
        await self.human.simulate_idle_activity(page)

        # ── Ambil info post ──
        post_info = await page.evaluate("""
            () => {
                // 1. Get Username first
                const headerLink = document.querySelector('header a[href^="/"], [role="link"] a[href^="/"]');
                const usernameEl = headerLink ? headerLink.querySelector('span') : null;
                let username = usernameEl ? usernameEl.textContent.trim() : '';
                
                if (!username) {
                    // Fallback username
                    const h1s = document.querySelectorAll('h1');
                    for (const h1 of h1s) {
                        const text = h1.textContent.trim();
                        if (text && !text.includes(' ') && text.length > 2) {
                            username = text;
                            break;
                        }
                    }
                }

                // 2. Get Caption / Description
                let caption = '';
                try {
                    // Cari semua time tags
                    const timeTags = document.querySelectorAll('time');
                    for (const timeEl of timeTags) {
                        const aLink = timeEl.closest('a');
                        // Caption time tag biasanya tidak memiliki parent a link, atau link ke post itu sendiri
                        if (!aLink || (aLink.getAttribute('href') && !aLink.getAttribute('href').includes('/c/'))) {
                            let container = timeEl.parentElement;
                            for (let depth = 0; depth < 8; depth++) {
                                if (!container) break;
                                
                                if (username && container.textContent.includes(username)) {
                                    const spans = container.querySelectorAll('span');
                                    for (const span of spans) {
                                        // Jangan ambil span yang ada time atau username didalamnya
                                        if (span.querySelector('time') || span.querySelector('span._ap3a')) continue;
                                        
                                        const text = span.textContent.trim();
                                        if (text && text !== username && !text.startsWith('•') && text !== 'Verified') {
                                            if (text.length > caption.length) {
                                                caption = text;
                                            }
                                        }
                                    }
                                }
                                if (caption) break;
                                container = container.parentElement;
                            }
                        }
                        if (caption) break;
                    }
                } catch(e) {}

                // Fallback 1: Cari span dir="auto" yang panjang di dalam article
                if (!caption) {
                    try {
                        const article = document.querySelector('article');
                        if (article) {
                            const spans = article.querySelectorAll('span[dir="auto"]');
                            for (const span of spans) {
                                const text = span.textContent.trim();
                                if (text.length > 20 && text !== username && !span.querySelector('a[href*="/p/"]')) {
                                    if (text.length > caption.length) {
                                        caption = text;
                                    }
                                }
                            }
                        }
                    } catch(e) {}
                }

                // Fallback 2: Meta description
                if (!caption) {
                    try {
                        const meta = document.querySelector('meta[property="og:description"]') || document.querySelector('meta[name="description"]');
                        if (meta) {
                            const content = meta.getAttribute('content') || '';
                            const match = content.match(/:\\s*"(.*)"/);
                            if (match) {
                                caption = match[1];
                            } else {
                                caption = content;
                            }
                        }
                    } catch(e) {}
                }

                // 3. Get Timestamp
                const timeEl = document.querySelector('time');
                const timestamp = timeEl ? (timeEl.getAttribute('datetime') || timeEl.textContent.trim()) : '';

                return {
                    username: username,
                    caption: caption,
                    timestamp: timestamp,
                    url: window.location.href,
                };
            }
        """)

        # ── Klik "View all comments" / "Load more" ──
        await self._expand_comments(page)

        # ── Scroll untuk load komentar ──
        scroll_times = max(3, max_comments // 8)
        last_comment_count = 0
        no_change_attempts = 0

        for _ in range(scroll_times):
            # Coba scroll di dalam scrollable container (dialog atau article pane)
            scrolled = await page.evaluate("""
                () => {
                    const roots = [
                        document.querySelector('div[role="dialog"]'),
                        document.querySelector('article'),
                        document.body
                    ];
                    for (const root of roots) {
                        if (!root) continue;
                        const elements = root.querySelectorAll('ul, div');
                        for (const el of elements) {
                            const style = window.getComputedStyle(el);
                            const isScrollable = style.overflowY === 'auto' || style.overflowY === 'scroll' || el.scrollHeight > el.clientHeight + 50;
                            if (isScrollable && el.clientHeight > 200 && el.clientHeight < window.innerHeight - 50) {
                                el.scrollTop = el.scrollHeight;
                                return true;
                            }
                        }
                    }
                    return false;
                }
            """)

            if not scrolled:
                # Kalau tidak menemukan scrollable pane, scroll halaman utama
                await self.human.scroll_page(page, times=1)

            # Delay dipercepat (dari 1.5-3.0 ke 1.0-2.0)
            await self.human.human_delay(1.0, 2.0)

            # Coba expand lagi setelah scroll
            await self._expand_comments(page)
            
            # Hitung jumlah komentar saat ini
            current_comment_count = await page.evaluate("""
                () => document.querySelectorAll('a[href*="/c/"]').length
            """)
            
            # Jika sudah mencapai max_comments, stop
            if current_comment_count >= max_comments:
                break
                
            # Jika tidak ada penambahan komentar baru, stop setelah 2x coba
            if current_comment_count == last_comment_count:
                no_change_attempts += 1
                if no_change_attempts >= 2:
                    break
            else:
                no_change_attempts = 0
                
            last_comment_count = current_comment_count

        # ── Extract komentar ──
        comments = await page.evaluate("""
            () => {
                const results = [];
                const seen = new Set();

                // ===== STRATEGI UTAMA =====
                // Cari semua link timestamp komentar (mengandung '/c/')
                const commentLinks = document.querySelectorAll('a[href*="/c/"]');

                for (const aLink of commentLinks) {
                    try {
                        const href = aLink.getAttribute('href') || '';
                        
                        // Get timestamp
                        const timeEl = aLink.querySelector('time');
                        const timestamp = timeEl ? (timeEl.getAttribute('datetime') || timeEl.textContent.trim()) : '';

                        // Naik ke parent container untuk mencari username dan text komentar
                        let container = aLink.parentElement;
                        let username = '';
                        let commentText = '';

                        for (let depth = 0; depth < 10; depth++) {
                            if (!container) break;

                            // 1. Cari username (span._ap3a)
                            const userSpan = container.querySelector('span._ap3a');
                            if (userSpan) {
                                username = userSpan.textContent.trim();
                            } else {
                                // Fallback: cari link profil user (starts with /, not containing p/reel/c/explore)
                                const allLinks = container.querySelectorAll('a[href^="/"]');
                                for (const a of allLinks) {
                                    const linkHref = a.getAttribute('href') || '';
                                    const cleanHref = linkHref.replace(/^\\/|\\/$/g, '');
                                    if (cleanHref && !linkHref.includes('/p/') && !linkHref.includes('/reel/') && !linkHref.includes('/c/') && !linkHref.includes('/explore/')) {
                                        username = cleanHref;
                                        break;
                                    }
                                }
                            }

                            // 2. Cari comment text
                            if (username) {
                                const spans = container.querySelectorAll('span');
                                for (const span of spans) {
                                    const text = span.textContent.trim();
                                    if (!text || text === username) continue;
                                    
                                    // Skip buttons / metadata
                                    if (['Reply', 'See translation', 'Delete', 'Report'].includes(text) || text.startsWith('View replies')) {
                                        continue;
                                    }

                                    // Skip if span contains time or username elements
                                    if (span.querySelector('time') || span.querySelector('span._ap3a')) continue;

                                    if (!commentText || text.length > commentText.length) {
                                        commentText = text;
                                    }
                                }
                            }

                            if (username && commentText) {
                                break;
                            }
                            container = container.parentElement;
                        }

                        if (username && commentText) {
                            const key = username + '::' + timestamp + '::' + commentText.substring(0, 50);
                            if (!seen.has(key)) {
                                seen.add(key);
                                results.push({
                                    username: username,
                                    text: commentText,
                                    timestamp: timestamp,
                                });
                            }
                        }
                    } catch(e) {}
                }

                // ===== STRATEGI FALLBACK =====
                // Jika tidak ada commentLinks (misal layout tanpa link /c/ atau login modal/guest view)
                // Coba fallback dengan mencari avatar img + text
                if (results.length === 0) {
                    const avatarImgs = document.querySelectorAll('img[alt*="profile picture"]');

                    for (const img of avatarImgs) {
                        try {
                            const altText = img.getAttribute('alt') || '';
                            const username = altText.replace(/'s profile picture$/i, '').trim();
                            if (!username) continue;

                            let container = img;
                            for (let i = 0; i < 12; i++) {
                                container = container.parentElement;
                                if (!container) break;
                                if (container.querySelector('time')) {
                                    break;
                                }
                            }

                            if (!container) continue;

                            const timeEl = container.querySelector('time');
                            const timestamp = timeEl ? (timeEl.getAttribute('datetime') || timeEl.textContent.trim()) : '';

                            let commentText = '';
                            const allSpans = container.querySelectorAll('span');
                            for (const span of allSpans) {
                                const text = span.textContent.trim();
                                if (!text || text === username) continue;
                                if (['Reply', 'See translation', 'Delete', 'Report'].includes(text) || text.startsWith('View replies')) {
                                    continue;
                                }
                                if (span.querySelector('time') || span.querySelector('img') || span.querySelector('svg')) continue;

                                if (!commentText || text.length > commentText.length) {
                                    commentText = text;
                                }
                            }

                            if (commentText) {
                                const key = username + '::' + timestamp + '::' + commentText.substring(0, 50);
                                if (!seen.has(key)) {
                                    seen.add(key);
                                    results.push({
                                        username: username,
                                        text: commentText,
                                        timestamp: timestamp,
                                    });
                                }
                            }
                        } catch(e) {}
                    }
                }

                return results;
            }
        """)

        # Dedup komentar (extra safety)
        seen = set()
        unique_comments = []
        for c in comments:
            key = f"{c['username']}:{c['text'][:50]}"
            if key not in seen:
                seen.add(key)
                unique_comments.append(c)

        # Format output
        post_data = {
            "platform": "instagram",
            "post_url": post_url,
            "post_id": post_url.split("/p/")[1].split("/")[0] if "/p/" in post_url else post_url.split("/reel/")[1].split("/")[0],
            "username": post_info.get("username", ""),
            "caption": post_info.get("caption", ""),
            "timestamp": post_info.get("timestamp", ""),
            "comments_count": len(unique_comments),
            "comments": unique_comments[:max_comments],
        }

        return post_data

    async def _expand_comments(self, page: Page):
        """Klik semua tombol untuk expand komentar."""
        expand_selectors = [
            # View all comments (paling penting)
            'div[role="button"]:has-text("View all")',
            'div[role="button"]:has-text("Lihat semua")',
            'div[role="button"]:has-text("View all comments")',
            'div[role="button"]:has-text("Lihat semua komentar")',
            # Load more
            'div[role="button"]:has-text("Load more")',
            'div[role="button"]:has-text("Muat lebih")',
            # Button fallback
            'button:has-text("View all")',
            'button:has-text("Load more")',
            'li button:has-text("more")',
        ]

        for _ in range(3):  # Cukup 3x per panggilan karena dipanggil berulang kali di scroll loop
            expanded = False
            for selector in expand_selectors:
                try:
                    btn = page.locator(selector).first
                    if await btn.is_visible(timeout=1500):
                        await btn.click()
                        await self.human.human_delay(1, 3)
                        expanded = True
                except Exception:
                    continue
            if not expanded:
                break
    # ─────────────────────────────────────────────
    # PROFILE + COMMENTS (tetap ada untuk fleksibilitas)
    # ─────────────────────────────────────────────
    async def _scrape_comments(self, task: ScrapingTask) -> ScrapingResult:
        """Ambil komentar dari satu post."""
        post_url = task.url
        max_comments = task.params.get("max_comments", 50)

        account = self._get_account()
        page, reuse_session = await self._get_or_create_page(account)

        try:
            post_data = await self._open_post_and_scrape(page, post_url, max_comments)

            # Flatten komentar untuk output
            flat_comments = []
            for comment in post_data.get("comments", []):
                comment["post_url"] = post_url
                comment["platform"] = "instagram"
                flat_comments.append(comment)

            return ScrapingResult(
                task_id=task.id,
                platform=self.platform_name,
                data=flat_comments,
                metadata={"post_url": post_url},
            )
        finally:
            await self.browser.close_session(page)

    async def _scrape_profile_posts(self, task: ScrapingTask) -> ScrapingResult:
        """Ambil daftar post dari profil."""
        profile_url = task.url
        max_posts = task.params.get("max_posts", 20)

        account = self._get_account()
        page, reuse_session = await self._get_or_create_page(account)

        try:
            await page.goto(profile_url, wait_until="domcontentloaded")
            await self.human.human_delay(2, 4)

            all_post_urls = set()
            no_new_count = 0

            for i in range(max_posts // 6 + 5):
                await self.human.scroll_page(page, times=1)
                await self.human.human_delay(1, 2)

                urls = await page.evaluate("""
                    () => {
                        const links = document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]');
                        return [...new Set([...links].map(a => a.href))];
                    }
                """)

                before = len(all_post_urls)
                all_post_urls.update(urls)
                after = len(all_post_urls)

                if after >= max_posts:
                    break
                if after == before:
                    no_new_count += 1
                    if no_new_count >= 2:
                        break
                else:
                    no_new_count = 0

            posts = []
            for url in list(all_post_urls)[:max_posts]:
                post_id = ""
                if "/p/" in url:
                    post_id = url.split("/p/")[1].split("/")[0]
                elif "/reel/" in url:
                    post_id = url.split("/reel/")[1].split("/")[0]
                posts.append({"url": url, "post_id": post_id, "platform": "instagram"})

            return ScrapingResult(
                task_id=task.id,
                platform=self.platform_name,
                data=posts,
                metadata={"profile_url": profile_url},
            )
        finally:
            await self.browser.close_session(page)

    async def _scrape_profile_info(self, task: ScrapingTask) -> ScrapingResult:
        """Ambil info dasar profil."""
        page, reuse_session = await self._get_or_create_page(None)
        try:
            await page.goto(task.url, wait_until="domcontentloaded")
            await self.human.human_delay(2, 4)

            info = await page.evaluate("""
                () => {
                    const meta = document.querySelector('meta[property="og:description"]');
                    const content = meta ? meta.content : '';
                    const f = content.match(/([\d,.]+[KMkm]?)\\s*Followers/i);
                    const g = content.match(/([\d,.]+[KMkm]?)\\s*Following/i);
                    const p = content.match(/([\d,.]+[KMkm]?)\\s*Posts/i);
                    return {
                        username: window.location.pathname.replace(/\\//g, ''),
                        posts_count: p ? p[1] : 'N/A',
                        followers: f ? f[1] : 'N/A',
                        following: g ? g[1] : 'N/A',
                        url: window.location.href,
                    };
                }
            """)
            return ScrapingResult(task_id=task.id, platform=self.platform_name, data=[info] if info else [])
        finally:
            await self.browser.close_session(page)

    # ─────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────
    def _get_account(self) -> Optional[dict]:
        accounts = self.config.get("accounts", {}).get("instagram", [])
        return random.choice(accounts) if accounts else None

    async def _get_or_create_page(self, account: Optional[dict]) -> tuple[Page, bool]:
        """Dapatkan page aktif (reused) atau buat session baru."""
        page = None
        reuse = False
        if hasattr(self, "active_page") and self.active_page:
            try:
                if not self.active_page.is_closed():
                    page = self.active_page
                    reuse = True
                    console.print("  [dim]Reusing active logged-in browser session[/dim]")
            except Exception:
                pass
            self.active_page = None # Reset agar tidak di-reuse berkali-kali secara salah

        if not page:
            page = await self.browser.create_session(
                cookies_file=f"instagram_{account['username']}.json" if account else None
            )
            
        return page, reuse