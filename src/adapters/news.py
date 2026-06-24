# src/adapters/news.py
"""Situs berita scraper — dengan search dan keyword filter."""

import asyncio
import re
from pathlib import Path
from typing import Optional
from datetime import datetime
import yaml
import httpx
from bs4 import BeautifulSoup
from readability import Document
from rich.console import Console
from playwright.async_api import Page

from src.adapters.base import BaseAdapter
from src.core.models import ScrapingTask, ScrapingResult
from src.core.keyword_filter import KeywordFilter

console = Console()


class NewsAdapter(BaseAdapter):
    platform_name = "news"

    def __init__(
        self,
        browser_manager,
        rate_limiter,
        proxy_pool,
        dedup,
        storage,
        config: dict,
        news_sources_path: str = "config/news_sources.yml",
    ):
        super().__init__(
            browser_manager=browser_manager,
            rate_limiter=rate_limiter,
            proxy_pool=proxy_pool,
            dedup=dedup,
            storage=storage,
            config=config,
        )
        self.sources = self._load_sources(news_sources_path)
        self.keyword_filter = KeywordFilter(news_sources_path)

    def _load_sources(self, path: str) -> dict:
        config_path = Path(path)
        if not config_path.exists():
            console.print(f"[yellow]⚠ News sources config not found: {path}[/yellow]")
            return {}
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return {s["name"]: s for s in data.get("sources", [])}

    async def scrape(self, task: ScrapingTask) -> ScrapingResult:
        handlers = {
            "article": self._scrape_article,
            "homepage": self._scrape_homepage,
            "search": self._scrape_search,
            "category": self._scrape_category,
        }
        handler = handlers.get(task.task_type)
        if not handler:
            raise ValueError(f"Unknown task type: {task.task_type}")
        return await handler(task)

    # ─────────────────────────────────────────────
    # SEARCH: Cari artikel berdasarkan keyword
    # INI METHOD BARU — paling penting untuk use case kamu
    # ─────────────────────────────────────────────
    async def _scrape_search(self, task: ScrapingTask) -> ScrapingResult:
        """
        Buka halaman search di situs berita,
        cari artikel berdasarkan keyword.
        """
        source_name = task.params.get("source", "")
        search_url = task.params.get("search_url", "")
        source_config = self.sources.get(source_name, {})
        selectors = source_config.get("selectors", {})

        if not search_url:
            raise ValueError("search_url harus diisi di params")

        console.print(f"  [cyan]🔍 Search: {search_url[:80]}...[/cyan]")

        page = await self.browser.create_session()

        try:
            await page.goto(search_url, wait_until="domcontentloaded")
            await self.human.human_delay(2, 4)
            await self.human.scroll_page(page, times=2)

            # Ambil link artikel dari hasil search
            html = await page.content()
            soup = BeautifulSoup(html, "lxml")

            link_selector = selectors.get("article_links", "a[href]")
            links = soup.select(link_selector)

            articles = []
            seen = set()
            base_url = source_config.get("base_url", "")

            for link in links:
                href = link.get("href", "")
                title = link.get_text(strip=True)

                if not href or href in seen or not title:
                    continue

                # Filter link yang bukan artikel
                if any(skip in href.lower() for skip in
                       ["/tag/", "/category/", "/login", "#", "javascript:"]):
                    continue

                # Normalize URL
                if href.startswith("/"):
                    href = base_url.rstrip("/") + href
                elif not href.startswith("http"):
                    continue

                # Filter URL Google (hanya ambil link berita asli)
                if "google.com" in href:
                    continue

                if href not in seen and len(title) > 10:
                    seen.add(href)
                    articles.append({
                        "url": href,
                        "title": title[:200],
                        "source": source_name,
                        "platform": "news",
                    })

            console.print(f"  [green]✓ {len(articles)} artikel ditemukan dari search[/green]")

            return ScrapingResult(
                task_id=task.id,
                platform=self.platform_name,
                data=articles,
                metadata={"source": source_name, "search_url": search_url},
            )

        finally:
            await self.browser.close_session(page)

    # ─────────────────────────────────────────────
    # HOMEPAGE / CATEGORY: Scrape + FILTER by keyword
    # ─────────────────────────────────────────────
    async def _scrape_homepage(self, task: ScrapingTask) -> ScrapingResult:
        source_name = task.params.get("source", "")
        source_config = self.sources.get(source_name)
        if not source_config:
            raise ValueError(f"Unknown news source: {source_name}")

        base_url = source_config["base_url"]
        selectors = source_config.get("selectors", {})
        article_selector = selectors.get("article_links", "a[href]")

        console.print(f"\n[cyan]🌐 Membuka {source_name}: {base_url}[/cyan]")

        page = await self.browser.create_session()

        try:
            await page.goto(base_url, wait_until="domcontentloaded")
            await self.human.human_delay(2, 4)
            await self.human.scroll_page(page, times=3)

            html = await page.content()
            soup = BeautifulSoup(html, "lxml")
            links = soup.select(article_selector)

            articles = []
            seen = set()

            for link in links:
                href = link.get("href", "")
                if not href:
                    continue

                if href.startswith("/"):
                    href = base_url.rstrip("/") + href
                elif not href.startswith("http"):
                    continue

                if not self._is_article_url(href):
                    continue

                if href not in seen:
                    seen.add(href)
                    title = link.get_text(strip=True)[:200]
                    articles.append({
                        "url": href,
                        "title": title,
                        "source": source_name,
                        "platform": "news",
                    })

            console.print(f"  [dim]{len(articles)} artikel ditemukan di homepage[/dim]")

            return ScrapingResult(
                task_id=task.id,
                platform=self.platform_name,
                data=articles,
                metadata={"source": source_name, "base_url": base_url},
            )

        finally:
            await self.browser.close_session(page)

    async def _scrape_category(self, task: ScrapingTask) -> ScrapingResult:
        return await self._scrape_homepage(task)

    # ─────────────────────────────────────────────
    # ARTICLE: Scrape konten + FILTER by keyword
    # ─────────────────────────────────────────────
    async def _scrape_article(self, task: ScrapingTask) -> ScrapingResult:
        url = task.url
        source_name = task.params.get("source", "")
        source_config = self.sources.get(source_name, {})
        selectors = source_config.get("selectors", {})

        console.print(f"  [cyan]📰 {url[:80]}...[/cyan]")

        page = await self.browser.create_session()

        try:
            await page.goto(url, wait_until="domcontentloaded")
            await self.human.human_delay(2, 4)
            await self.human.scroll_page(page, times=3)

            html = await page.content()
            if not html or len(html) < 500:
                raise RuntimeError(f"Response terlalu pendek ({len(html)})")

            full_soup = BeautifulSoup(html, "lxml")

            # TITLE
            title = self._extract(full_soup, selectors.get("title", "h1"))
            if not title:
                og = full_soup.select_one('meta[property="og:title"]')
                title = og.get("content", "") if og else ""
            if not title:
                try:
                    title = Document(html).title()
                except Exception:
                    title = "Untitled"

            # CONTENT
            content = self._extract(full_soup, selectors.get("content", "article"))
            if not content or len(content) < 50:
                try:
                    doc = Document(html)
                    content = BeautifulSoup(doc.summary(), "lxml").get_text("\n", strip=True)
                except Exception:
                    pass
            if not content or len(content) < 50:
                paras = full_soup.find_all("p")
                content = "\n".join(p.get_text(strip=True) for p in paras
                                   if len(p.get_text(strip=True)) > 20)

            # AUTHOR
            author = self._extract(full_soup, selectors.get("author", ""))
            if not author:
                m = full_soup.select_one('meta[name="author"]')
                author = m.get("content", "") if m else ""

            # DATE — cek beberapa sumber
            date = self._extract(full_soup, selectors.get("date", ""))
            if not date:
                for meta_sel in [
                    'meta[property="article:published_time"]',
                    'meta[name="publishdate"]',
                    'meta[name="date"]',
                    'meta[itemprop="datePublished"]',
                    'time[datetime]',
                ]:
                    m = full_soup.select_one(meta_sel)
                    if m:
                        date = m.get("datetime", "") or m.get("content", "") or m.get_text(strip=True)
                        if date:
                            break

            # DESCRIPTION / OG:description
            description = ""
            og_desc = full_soup.select_one('meta[property="og:description"]')
            if og_desc:
                description = og_desc.get("content", "")

            article = {
                "source": source_name or url.split("/")[2],
                "url": url,
                "title": title,
                "content": (content or "")[:10000],
                "description": description[:500],
                "author": author,
                "date": date,
                "word_count": len(content.split()) if content else 0,
                "platform": "news",
            }

            # CEK: apakah ada "Solusiku"?
            is_relevant, score, matched_w, matched_k = self.keyword_filter.is_relevant(article)

            if is_relevant:
                article["relevance_score"] = score
                article["matched_wajib"] = matched_w
                article["matched_konteks"] = matched_k
                console.print(
                    f"    [bold green]✓ RELEVAN (skor {score}): {title[:55]}[/bold green]"
                )
                if matched_k:
                    console.print(f"      [dim]Konteks: {', '.join(matched_k[:5])}[/dim]")
            else:
                console.print(f"    [dim]✗ Tidak ada 'Solusiku': {title[:55]}[/dim]")

            return ScrapingResult(
                task_id=task.id,
                platform=self.platform_name,
                data=[article],
                metadata={"source": source_name, "relevant": is_relevant},
            )

        except Exception as e:
            raise RuntimeError(f"Gagal scrape: {e}")
        finally:
            await self.browser.close_session(page)
    def _extract(self, soup: BeautifulSoup, selector: str) -> str:
        if not selector:
            return ""
        for sel in selector.split(","):
            el = soup.select_one(sel.strip())
            if el and el.get_text(strip=True):
                return el.get_text(strip=True)
        return ""

    def _is_article_url(self, url: str) -> bool:
        url_lower = url.lower()
        skip = ["/tag/", "/category/", "/author/", "/page/", "/search",
                "/login", "/register", "/about", "/contact", "/privacy",
                "/terms", "#", ".jpg", ".png", ".gif", ".pdf", "/feed", "/rss"]
        for s in skip:
            if s in url_lower:
                return False
        has_date = any(x in url_lower for x in ["/2024/", "/2025/", "/2026/"])
        has_slug = len(url.split("/")[-1]) > 15
        return has_date or has_slug