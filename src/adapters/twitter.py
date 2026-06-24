# src/adapters/twitter.py
"""Twitter/X browser-based scraper."""

import asyncio
import json
from rich.console import Console
from playwright.async_api import Page

from src.adapters.base import BaseAdapter
from src.core.models import ScrapingTask, ScrapingResult, TaskStatus

console = Console()


class TwitterAdapter(BaseAdapter):
    platform_name = "twitter"

    async def scrape(self, task: ScrapingTask) -> ScrapingResult:
        handlers = {
            "profile_tweets": self._scrape_profile_tweets,
            "search": self._scrape_search,
            "tweet_replies": self._scrape_tweet_replies,
        }

        handler = handlers.get(task.task_type)
        if not handler:
            raise ValueError(f"Unknown task type: {task.task_type}")

        return await handler(task)

    async def _scrape_profile_tweets(self, task: ScrapingTask) -> ScrapingResult:
        """Ambil tweets dari profil Twitter."""
        profile_url = task.url
        max_tweets = task.params.get("max_tweets", 50)

        page = await self.browser.create_session()

        try:
            await page.goto(profile_url, wait_until="domcontentloaded")
            await self.human.human_delay(3, 5)

            tweets = []
            last_height = 0
            no_new_count = 0

            while len(tweets) < max_tweets and no_new_count < 3:
                # Scroll untuk load tweets
                await self.human.scroll_page(page, times=2)
                await self.human.human_delay(1, 3)

                # Extract tweets dari DOM
                new_tweets = await page.evaluate("""
                    () => {
                        const results = [];
                        const tweetArticles = document.querySelectorAll('article[data-testid="tweet"]');

                        for (const article of tweetArticles) {
                            try {
                                const userEl = article.querySelector('div[data-testid="User-Name"]');
                                const textEl = article.querySelector('div[data-testid="tweetText"]');
                                const timeEl = article.querySelector('time');
                                const linkEl = article.querySelector('a[href*="/status/"]');

                                // Extract stats
                                const replyBtn = article.querySelector('button[data-testid="reply"]');
                                const retweetBtn = article.querySelector('button[data-testid="retweet"]');
                                const likeBtn = article.querySelector('button[data-testid="like"]');

                                const username = userEl ? userEl.textContent.trim().split('@')[1]?.split('·')[0]?.trim() : '';
                                const displayName = userEl ? userEl.textContent.trim().split('@')[0]?.trim() : '';

                                if (textEl) {
                                    results.push({
                                        username: username || 'unknown',
                                        display_name: displayName || '',
                                        text: textEl.textContent.trim(),
                                        timestamp: timeEl ? timeEl.getAttribute('datetime') : null,
                                        tweet_url: linkEl ? linkEl.href : '',
                                        likes: likeBtn ? likeBtn.textContent.trim() : '0',
                                        retweets: retweetBtn ? retweetBtn.textContent.trim() : '0',
                                        replies: replyBtn ? replyBtn.textContent.trim() : '0',
                                        platform: 'twitter',
                                    });
                                }
                            } catch (e) {
                                // Skip tweet yang gagal di-parse
                            }
                        }

                        return results;
                    }
                """)

                # Tambah yang baru (dedup by tweet_url)
                existing_urls = {t.get("tweet_url") for t in tweets}
                added = 0
                for tweet in new_tweets:
                    if tweet.get("tweet_url") and tweet["tweet_url"] not in existing_urls:
                        tweets.append(tweet)
                        existing_urls.add(tweet["tweet_url"])
                        added += 1

                if added == 0:
                    no_new_count += 1
                else:
                    no_new_count = 0

                console.print(f"  [dim]Tweets collected: {len(tweets)}/{max_tweets} (+{added})[/dim]")

            return ScrapingResult(
                task_id=task.id,
                platform=self.platform_name,
                data=tweets[:max_tweets],
                metadata={"profile_url": profile_url},
            )

        finally:
            await self.browser.close_session(page)

    async def _scrape_search(self, task: ScrapingTask) -> ScrapingResult:
        """Scrape hasil pencarian Twitter."""
        query = task.params.get("query", "")
        max_results = task.params.get("max_results", 30)
        search_url = f"https://x.com/search?q={query}&src=typed_query&f=live"

        page = await self.browser.create_session()

        try:
            await page.goto(search_url, wait_until="domcontentloaded")
            await self.human.human_delay(3, 5)

            # Cek apakah perlu login
            if "login" in page.url.lower():
                console.print("[yellow]⚠ Search butuh login di Twitter/X[/yellow]")
                return ScrapingResult(
                    task_id=task.id,
                    platform=self.platform_name,
                    data=[],
                    error="Login required for search",
                )

            tweets = []
            no_new_count = 0

            while len(tweets) < max_results and no_new_count < 3:
                await self.human.scroll_page(page, times=2)
                await self.human.human_delay(1, 3)

                new_tweets = await page.evaluate("""
                    () => {
                        const results = [];
                        const articles = document.querySelectorAll('article[data-testid="tweet"]');

                        for (const article of articles) {
                            try {
                                const userEl = article.querySelector('div[data-testid="User-Name"]');
                                const textEl = article.querySelector('div[data-testid="tweetText"]');
                                const timeEl = article.querySelector('time');
                                const linkEl = article.querySelector('a[href*="/status/"]');

                                results.push({
                                    username: userEl ? userEl.textContent.split('@')[1]?.split(/[·\\s]/)[0] : '',
                                    text: textEl ? textEl.textContent.trim() : '',
                                    timestamp: timeEl ? timeEl.getAttribute('datetime') : null,
                                    tweet_url: linkEl ? linkEl.href : '',
                                    platform: 'twitter',
                                });
                            } catch(e) {}
                        }
                        return results;
                    }
                """)

                existing = {t["tweet_url"] for t in tweets if t.get("tweet_url")}
                added = 0
                for t in new_tweets:
                    if t.get("tweet_url") and t["tweet_url"] not in existing:
                        tweets.append(t)
                        added += 1

                no_new_count = 0 if added > 0 else no_new_count + 1

            return ScrapingResult(
                task_id=task.id,
                platform=self.platform_name,
                data=tweets[:max_results],
                metadata={"query": query},
            )

        finally:
            await self.browser.close_session(page)

    async def _scrape_tweet_replies(self, task: ScrapingTask) -> ScrapingResult:
        """Ambil replies dari satu tweet."""
        tweet_url = task.url
        max_replies = task.params.get("max_replies", 50)

        page = await self.browser.create_session()

        try:
            await page.goto(tweet_url, wait_until="domcontentloaded")
            await self.human.human_delay(3, 5)

            replies = []
            no_new_count = 0

            while len(replies) < max_replies and no_new_count < 3:
                await self.human.scroll_page(page, times=2)
                await self.human.human_delay(1.5, 3)

                new_replies = await page.evaluate("""
                    () => {
                        const results = [];
                        const articles = document.querySelectorAll('article[data-testid="tweet"]');

                        // Skip article pertama (itu tweet asli)
                        for (let i = 1; i < articles.length; i++) {
                            const article = articles[i];
                            try {
                                const userEl = article.querySelector('div[data-testid="User-Name"]');
                                const textEl = article.querySelector('div[data-testid="tweetText"]');
                                const timeEl = article.querySelector('time');

                                if (textEl) {
                                    results.push({
                                        username: userEl ? userEl.textContent.split('@')[1]?.split(/[·\\s]/)[0] : '',
                                        text: textEl.textContent.trim(),
                                        timestamp: timeEl ? timeEl.getAttribute('datetime') : null,
                                        platform: 'twitter',
                                        reply_to: window.location.href,
                                    });
                                }
                            } catch(e) {}
                        }
                        return results;
                    }
                """)

                existing = {r["text"][:50] for r in replies}
                added = 0
                for r in new_replies:
                    if r["text"][:50] not in existing:
                        replies.append(r)
                        added += 1

                no_new_count = 0 if added > 0 else no_new_count + 1

            return ScrapingResult(
                task_id=task.id,
                platform=self.platform_name,
                data=replies[:max_replies],
                metadata={"tweet_url": tweet_url},
            )

        finally:
            await self.browser.close_session(page)