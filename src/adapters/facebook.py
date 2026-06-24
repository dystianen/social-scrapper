# src/adapters/facebook.py
"""Facebook browser-based scraper."""

import asyncio
import random
from rich.console import Console

from src.adapters.base import BaseAdapter
from src.core.models import ScrapingTask, ScrapingResult

console = Console()


class FacebookAdapter(BaseAdapter):
    platform_name = "facebook"

    async def login(self, username: str, password: str) -> bool:
        """Login ke Facebook."""
        console.print(f"[cyan]🔐 Login Facebook: {username}[/cyan]")
        page = await self.browser.create_session()

        try:
            await page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")
            await self.human.human_delay(2, 4)

            await page.wait_for_selector('#email', timeout=10000)
            await self.human.type_text(page, '#email', username)
            await self.human.human_delay(0.3, 0.8)
            await self.human.type_text(page, '#pass', password)
            await self.human.human_delay(0.5, 1.5)
            await self.human.click_element(page, 'button[name="login"]')
            await self.human.human_delay(4, 7)

            # Cek login success
            if "login" in page.url.lower():
                console.print("[red]✗ Facebook login gagal[/red]")
                return False

            # Handle checkpoint/dialog
            try:
                skip = page.locator('button:has-text("Not Now"), a:has-text("Not Now")').first
                if await skip.is_visible(timeout=3000):
                    await skip.click()
                    await self.human.human_delay(1, 2)
            except Exception:
                pass

            await self.browser.close_session(page, save_cookies_as=f"facebook_{username}.json")
            console.print(f"[green]✓ Facebook login berhasil: {username}[/green]")
            return True

        except Exception as e:
            console.print(f"[red]✗ Facebook login error: {e}[/red]")
            await self.browser.close_session(page)
            return False

    async def scrape(self, task: ScrapingTask) -> ScrapingResult:
        handlers = {
            "page_posts": self._scrape_page_posts,
            "post_comments": self._scrape_post_comments,
        }

        handler = handlers.get(task.task_type)
        if not handler:
            raise ValueError(f"Unknown task type: {task.task_type}")

        return await handler(task)

    async def _scrape_page_posts(self, task: ScrapingTask) -> ScrapingResult:
        """Ambil post dari halaman Facebook."""
        page_url = task.url
        max_posts = task.params.get("max_posts", 20)

        account = self._get_random_account()
        page = await self.browser.create_session(
            cookies_file=f"facebook_{account['username']}.json" if account else None
        )

        try:
            await page.goto(page_url, wait_until="domcontentloaded")
            await self.human.human_delay(3, 5)

            posts = []
            no_new_count = 0

            while len(posts) < max_posts and no_new_count < 3:
                await self.human.scroll_page(page, times=2)
                await self.human.human_delay(2, 4)

                new_posts = await page.evaluate("""
                    () => {
                        const results = [];
                        // Facebook sering ganti struktur, ini selector yang cukup universal
                        const postDivs = document.querySelectorAll('[data-ad-rendering-role="story_message"], div[class*="userContent"], article');

                        for (const div of postDivs) {
                            try {
                                const textEl = div.querySelector('[data-ad-preview="message"], [data-testid="post_message"]');
                                const timeEl = div.querySelector('a[href*="/permalink"], abbr[data-utime], span[id*="jsc"]');
                                const authorEl = div.querySelector('h3 a, h4 a, strong a');

                                if (textEl || authorEl) {
                                    results.push({
                                        author: authorEl ? authorEl.textContent.trim() : '',
                                        text: textEl ? textEl.textContent.trim() : '',
                                        timestamp: timeEl ? timeEl.textContent.trim() : '',
                                        post_url: timeEl && timeEl.tagName === 'A' ? timeEl.href : '',
                                        platform: 'facebook',
                                    });
                                }
                            } catch(e) {}
                        }
                        return results;
                    }
                """)

                existing = {p["text"][:80] for p in posts}
                added = 0
                for p in new_posts:
                    if p["text"][:80] not in existing:
                        posts.append(p)
                        added += 1

                no_new_count = 0 if added > 0 else no_new_count + 1

            return ScrapingResult(
                task_id=task.id,
                platform=self.platform_name,
                data=posts[:max_posts],
                metadata={"page_url": page_url},
            )

        finally:
            await self.browser.close_session(page)

    async def _scrape_post_comments(self, task: ScrapingTask) -> ScrapingResult:
        """Ambil komentar dari satu post Facebook."""
        post_url = task.url
        max_comments = task.params.get("max_comments", 50)

        account = self._get_random_account()
        page = await self.browser.create_session(
            cookies_file=f"facebook_{account['username']}.json" if account else None
        )

        try:
            await page.goto(post_url, wait_until="domcontentloaded")
            await self.human.human_delay(3, 5)

            # Klik "View more comments" / "See more"
            for _ in range(5):
                try:
                    more = page.locator('div[role="button"]:has-text("View more"), div[role="button"]:has-text("See more"), a:has-text("View more")').first
                    if await more.is_visible(timeout=2000):
                        await self.human.click_element(
                            page,
                            'div[role="button"]:has-text("View more"), div[role="button"]:has-text("See more"), a:has-text("View more")'
                        )
                        await self.human.human_delay(1, 3)
                    else:
                        break
                except Exception:
                    break

            await self.human.scroll_page(page, times=3)

            comments = await page.evaluate("""
                () => {
                    const results = [];
                    // Facebook comment selectors (sering berubah)
                    const commentDivs = document.querySelectorAll('[data-testid="UFI2Comment/body"], div[class*="comment"] span');

                    for (const div of commentDivs) {
                        try {
                            const authorEl = div.querySelector('a[role="link"] span, h4 a, a[class*="profile"]');
                            const textEl = div.querySelector('span[dir="auto"]');

                            if (textEl) {
                                results.push({
                                    username: authorEl ? authorEl.textContent.trim() : '',
                                    text: textEl.textContent.trim(),
                                    platform: 'facebook',
                                    post_url: window.location.href,
                                });
                            }
                        } catch(e) {}
                    }
                    return results;
                }
            """)

            return ScrapingResult(
                task_id=task.id,
                platform=self.platform_name,
                data=comments[:max_comments],
                metadata={"post_url": post_url},
            )

        finally:
            await self.browser.close_session(page)

    def _get_random_account(self):
        accounts = self.config.get("accounts", {}).get("facebook", [])
        return random.choice(accounts) if accounts else None