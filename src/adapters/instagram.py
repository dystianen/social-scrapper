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

    # ─────────────────────────────────────────────
    # LOGIN + OTP
    # ─────────────────────────────────────────────
    async def login(self, username: str, password: str, email: str = "") -> bool:
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

        page = await self.browser.create_session()

        try:
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

            # Tunggu form login
            await page.wait_for_selector('input[name="username"]', timeout=15000)

            # Ketik username
            await self.human.type_text(page, 'input[name="username"]', username)
            await self.human.human_delay(0.5, 1.0)

            # Ketik password
            await self.human.type_text(page, 'input[name="password"]', password)
            await self.human.human_delay(0.5, 1.5)

            # Klik login
            await self.human.click_element(page, 'button[type="submit"]')
            await self.human.human_delay(4, 7)

            # ── CEK: Apakah perlu OTP? ──
            otp_needed = await self._check_if_otp_needed(page)

            if otp_needed:
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
                else:
                    console.print("[red]✗ Tidak ada OTP, login gagal[/red]")
                    return False

            # ── CEK: Login berhasil? ──
            current_url = page.url
            if "/accounts/login" in current_url:
                # Mungkin ada error message
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

            # ── Handle dialog popups ──
            await self._dismiss_dialogs(page)

            # ── Simpan cookies ──
            await self.browser.close_session(page, save_cookies_as=f"instagram_{username}.json")
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
            'text="Enter the code"',
            'text="Enter Security Code"',
            'text="Masukkan kode"',
            'text="Confirm"',
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

        if otp_input:
            await self.human.type_text(page, otp_input, otp_code)
            await self.human.human_delay(0.5, 1.0)

            # Klik tombol confirm/submit
            confirm_selectors = [
                'button:has-text("Confirm")',
                'button:has-text("Submit")',
                'button:has-text("Verify")',
                'button:has-text("Konfirmasi")',
                'button[type="submit"]',
            ]

            for sel in confirm_selectors:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=2000):
                        await self.human.click_element(page, sel)
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
        """Tutup semua dialog popup setelah login."""
        dialogs = [
            'button:has-text("Not Now")',
            'button:has-text("Save Info")',
            'button:has-text("Save Your Login Info")',
            'button:has-text("Not now")',
            'button:has-text("Turn on Notifications")',
            'button:has-text("Cancel")',
        ]

        for selector in dialogs:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=3000):
                    await btn.click()
                    console.print(f"  [dim]Dialog dismissed: {selector}[/dim]")
                    await self.human.human_delay(1, 2)
            except Exception:
                continue

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

        page = await self.browser.create_session(
            cookies_file=f"instagram_{account['username']}.json" if account else None
        )

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

        # Baca post dulu (manusia baca dulu sebelum scroll ke komentar)
        await self.human.simulate_idle_activity(page)

        # ── Ambil info post ──
        post_info = await page.evaluate("""
            () => {
                // Caption / text post
                const captionEl = document.querySelector('h1, div[class*="_a9zs"] span, div[data-testid="post-comment-root"]');
                const caption = captionEl ? captionEl.textContent.trim() : '';

                // Username
                const userEl = document.querySelector('header a span, a[role="link"] span');
                const username = userEl ? userEl.textContent.trim() : '';

                // Timestamp
                const timeEl = document.querySelector('time');
                const timestamp = timeEl ? timeEl.getAttribute('datetime') || timeEl.textContent.trim() : '';

                // Likes
                const likeSection = document.querySelector('section span[class*="html-span"]');
                const likes = likeSection ? likeSection.textContent.trim() : '';

                return {
                    username: username,
                    caption: caption,
                    timestamp: timestamp,
                    likes: likes,
                    url: window.location.href,
                };
            }
        """)

        # ── Klik "View all comments" / "Load more" ──
        await self._expand_comments(page)

        # ── Scroll untuk load komentar ──
        scroll_times = max(3, max_comments // 8)
        await self.human.scroll_page(page, times=scroll_times)

        # ── Extract komentar ──
        comments = await page.evaluate("""
            () => {
                const results = [];

                // Cari elemen komentar
                const commentItems = document.querySelectorAll('ul ul li, div[role="button"] + div[role="button"]');

                // Strategy 1: ul > ul > li
                const listItems = document.querySelectorAll('ul ul li');
                for (const li of listItems) {
                    try {
                        const userLink = li.querySelector('a[href*="/"] span');
                        const allSpans = li.querySelectorAll('span');
                        const timeEl = li.querySelector('time');

                        if (userLink) {
                            const username = userLink.textContent.trim();

                            // Ambil text komentar (span terpanjang yang bukan username)
                            let commentText = '';
                            for (const span of allSpans) {
                                const text = span.textContent.trim();
                                if (text.length > commentText.length &&
                                    text !== username &&
                                    !text.match(/^\d+[hdwm]$/)) {
                                    commentText = text;
                                }
                            }

                            if (commentText && commentText.length > 1) {
                                results.push({
                                    username: username,
                                    text: commentText,
                                    timestamp: timeEl ? (timeEl.getAttribute('datetime') || timeEl.textContent.trim()) : '',
                                });
                            }
                        }
                    } catch(e) {}
                }

                // Strategy 2: Kalau strategy 1 dapat sedikit
                if (results.length < 2) {
                    const allSpans = document.querySelectorAll('article span[dir="auto"]');
                    // Group spans by parent
                    for (const span of allSpans) {
                        const text = span.textContent.trim();
                        if (text.length > 3 && !text.match(/^\d+[hdwm]$/)) {
                            const parent = span.closest('li, div[role="button"]');
                            if (parent) {
                                const userLink = parent.querySelector('a[href*="/"]');
                                if (userLink) {
                                    const username = userLink.textContent.trim();
                                    const alreadyExists = results.some(r => r.text === text);
                                    if (!alreadyExists && text !== username) {
                                        results.push({
                                            username: username,
                                            text: text,
                                            timestamp: '',
                                        });
                                    }
                                }
                            }
                        }
                    }
                }

                return results;
            }
        """)

        # Dedup komentar
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
            "likes": post_info.get("likes", ""),
            "comments_count": len(unique_comments),
            "comments": unique_comments[:max_comments],
        }

        return post_data

    async def _expand_comments(self, page: Page):
        """Klik semua tombol untuk expand komentar."""
        expand_selectors = [
            'button:has-text("View all")',
            'button:has-text("Load more")',
            'button:has-text("View replies")',
            'button:has-text("Lihat semua")',
            'button:has-text("Lihat komentar")',
            'li button:has-text("more")',
            'div[role="button"]:has-text("View all")',
            'div[role="button"]:has-text("Load more")',
        ]

        for _ in range(5):  # Maksimal 5 kali expand
            expanded = False
            for selector in expand_selectors:
                try:
                    btn = page.locator(selector).first
                    if await btn.is_visible(timeout=1500):
                        await self.human.click_element(page, selector)
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
        page = await self.browser.create_session(
            cookies_file=f"instagram_{account['username']}.json" if account else None
        )

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
        page = await self.browser.create_session(
            cookies_file=f"instagram_{account['username']}.json" if account else None
        )

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
        page = await self.browser.create_session()
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