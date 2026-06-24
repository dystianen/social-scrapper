# src/orchestrator/scheduler.py
"""Task scheduler & orchestrator — otak dari semuanya."""

import asyncio
from datetime import datetime
from typing import Optional
from collections import defaultdict
from rich.console import Console
from rich.table import Table

from src.core.models import ScrapingTask, ScrapingResult, TaskStatus
from src.core.browser_manager import BrowserManager
from src.core.rate_limiter import RateLimiter
from src.core.proxy_pool import ProxyPool
from src.core.dedup import Deduplication
from src.core.storage import Storage
from src.adapters.instagram import InstagramAdapter
from src.adapters.twitter import TwitterAdapter
from src.adapters.facebook import FacebookAdapter
from src.adapters.news import NewsAdapter
from src.core.keyword_filter import KeywordFilter

console = Console()


class ScrapingOrchestrator:
    """
    Koordinator utama.
    Menerima task, mendistribusikan ke adapter yang tepat,
    mengelola concurrency, dan melaporkan hasil.
    """

    def __init__(self, config: dict):
        self.config = config
        self.results: list[ScrapingResult] = []
        self._running = False

        # Inisialisasi core components
        browser_config = config.get("browser", {})
        browser_config["cookies_dir"] = config.get("general", {}).get("cookies_dir", "./data/cookies")
        self.browser_manager = BrowserManager(browser_config)

        proxy_config = config.get("proxy", {})
        self.proxy_pool = ProxyPool(
            proxies=proxy_config.get("pool", []),
            enabled=proxy_config.get("enabled", False),
        )

        rate_config = config.get("rate_limit", {})
        self.rate_limiter = RateLimiter(
            max_requests=rate_config.get("default_max_requests", 30),
            per_seconds=rate_config.get("default_per_seconds", 60),
        )

        self.dedup = Deduplication(
            db_path=config.get("general", {}).get("cache_dir", "./data/cache") + "/dedup_hashes.txt"
        )

        self.storage = Storage({
            "db_path": config.get("storage", {}).get("db_path", "./data/cache/scraper.db"),
            "output_dir": config.get("general", {}).get("output_dir", "./data/output"),
        })

        # Inisialisasi adapters
        self.adapters = {}

    async def initialize(self):
        """Inisialisasi semua komponen."""
        console.print("\n[bold cyan]🚀 Initializing Scraping Orchestrator...[/bold cyan]\n")

        await self.browser_manager.initialize()
        await self.storage.initialize()

        # Buat adapters
        common_args = {
            "browser_manager": self.browser_manager,
            "rate_limiter": self.rate_limiter,
            "proxy_pool": self.proxy_pool,
            "dedup": self.dedup,
            "storage": self.storage,
            "config": self.config,
        }

        self.adapters = {
            "instagram": InstagramAdapter(**common_args),
            "twitter": TwitterAdapter(**common_args),
            "facebook": FacebookAdapter(**common_args),
            "news": NewsAdapter(
                **common_args,
                news_sources_path="config/news_sources.yml",
            ),
        }

        console.print("[green]✓ All adapters ready[/green]")
        console.print(f"[dim]  Platforms: {', '.join(self.adapters.keys())}[/dim]")
        console.print(f"[dim]  Browser headless: {self.config.get('browser', {}).get('headless', False)}[/dim]")
        console.print(f"[dim]  Max browser instances: {self.config.get('browser', {}).get('max_instances', 3)}[/dim]")
        console.print(f"[dim]  Proxy enabled: {self.proxy_pool.enabled}[/dim]")
        console.print()

    async def shutdown(self):
        """Cleanup semua resource."""
        console.print("\n[yellow]Shutting down...[/yellow]")

        self.dedup.save()

        try:
            await self.storage.close()
        except Exception as e:
            console.print(f"[dim]Storage close warning: {e}[/dim]")

        try:
            await self.browser_manager.shutdown()
        except Exception as e:
            console.print(f"[dim]Browser close warning: {e}[/dim]")

        console.print("[yellow]✓ Shutdown complete[/yellow]\n")

    async def run_tasks(self, tasks: list[ScrapingTask], concurrency: int = 3):
        """
        Jalankan semua task dengan concurrency terbatas.
        """
        self._running = True

        # Sort by priority
        tasks.sort(key=lambda t: t.priority)

        console.print(f"\n[bold]📋 Running {len(tasks)} tasks (concurrency: {concurrency})[/bold]\n")

        # Group tasks by platform
        by_platform = defaultdict(list)
        for task in tasks:
            by_platform[task.platform].append(task)

        for platform, platform_tasks in by_platform.items():
            console.print(f"[cyan]  {platform}: {len(platform_tasks)} tasks[/cyan]")

        console.print()

        # Semaphore untuk batasi concurrent tasks
        semaphore = asyncio.Semaphore(concurrency)
        completed = 0
        total = len(tasks)

        async def run_one(task: ScrapingTask):
            nonlocal completed
            async with semaphore:
                adapter = self.adapters.get(task.platform)
                if not adapter:
                    console.print(f"[red]✗ Unknown platform: {task.platform}[/red]")
                    return

                result = await adapter.execute(task)
                self.results.append(result)
                completed += 1

                progress = f"[{completed}/{total}]"
                status_color = "green" if result.status == TaskStatus.SUCCESS else "red"
                console.print(f"  [{status_color}]{progress} {task.platform}/{task.task_type} → {result.count} items[/{status_color}]")

        # Jalankan semua task
        await asyncio.gather(*[run_one(task) for task in tasks], return_exceptions=True)

        # Print summary
        self._print_summary()

    async def login_platforms(self, platforms: Optional[list[str]] = None):
        """Login ke platform yang butuh authentication."""
        if platforms is None:
            platforms = ["instagram", "facebook"]

        for platform in platforms:
            accounts = self.config.get("accounts", {}).get(platform, [])
            if not accounts:
                console.print(f"[yellow]⚠ No accounts configured for {platform}[/yellow]")
                continue

            adapter = self.adapters.get(platform)
            if not adapter or not hasattr(adapter, "login"):
                console.print(f"[yellow]⚠ {platform} adapter doesn't support login[/yellow]")
                continue

            # Login dengan akun pertama
            account = accounts[0]
            await adapter.login(account["username"], account["password"])

    async def run_news_search(self):
        """
        Scrape SEMUA berita yang mengandung kata 'Solusiku'.
        """
        news_adapter = self.adapters.get("news")
        if not news_adapter:
            console.print("[red]✗ News adapter tidak tersedia[/red]")
            return []

        kf = news_adapter.keyword_filter

        console.print("\n" + "=" * 60)
        console.print("[bold cyan]📰 SCRAPING BERITA SOLUSIKU[/bold cyan]")
        console.print("=" * 60)
        console.print(f"  [dim]Wajib ada : {', '.join(kf.wajib)}[/dim]")
        console.print(f"  [dim]Konteks   : {', '.join(kf.konteks[:8])}...[/dim]")
        console.print()

        # ── PHASE 1: Search di semua situs ──
        search_tasks = []
        for source_name, source_config in news_adapter.sources.items():
            for search_url in source_config.get("search_urls", []):
                search_tasks.append(ScrapingTask(
                    platform="news", url="",
                    task_type="search",
                    params={"source": source_name, "search_url": search_url},
                    priority=1,
                ))

        console.print(f"[bold]📋 Phase 1: {len(search_tasks)} search queries[/bold]\n")
        self.results = []
        await self.run_tasks(search_tasks)

        # Kumpulkan & dedup URL
        article_urls = []
        seen = set()
        for result in self.results:
            if result.data:
                for item in result.data:
                    url = item.get("url", "")
                    if url and url not in seen:
                        seen.add(url)
                        article_urls.append({
                            "url": url,
                            "source": item.get("source", ""),
                        })

        console.print(f"\n[bold]📋 {len(article_urls)} artikel unik ditemukan dari search[/bold]")

        # ── PHASE 2: Buka setiap artikel, ambil konten ──
        all_articles = []
        if article_urls:
            article_tasks = [
                ScrapingTask(
                    platform="news", url=a["url"],
                    task_type="article",
                    params={"source": a["source"]},
                    priority=5,
                )
                for a in article_urls
            ]

            console.print(f"\n[bold]📋 Phase 2: Membuka {len(article_tasks)} artikel...[/bold]\n")
            self.results = []
            await self.run_tasks(article_tasks)

            for result in self.results:
                if result.data:
                    all_articles.extend(result.data)

        # ── PHASE 3: Filter — WAJIB ada "Solusiku" ──
        relevant = kf.filter_articles(all_articles)

        # ── EXPORT ──
        if relevant:
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            json_path = self.storage.export_json(relevant, f"solusiku_{timestamp}.json")
            csv_path = self.storage.export_csv(relevant, f"solusiku_{timestamp}.csv")

            # Ringkasan
            console.print("\n" + "=" * 60)
            console.print(f"[bold green]✅ {len(relevant)} BERITA SOLUSIKU DITEMUKAN:[/bold green]")
            console.print("=" * 60)

            for i, a in enumerate(relevant, 1):
                skor = a.get("relevance_score", 0)
                konteks = ", ".join(a.get("matched_konteks", [])[:4])
                console.print(f"\n  [cyan]{i}.[/cyan] [{a['source']}] {a['title'][:65]}")
                console.print(f"     [dim]Skor: {skor} | Konteks: {konteks}[/dim]")
                console.print(f"     [dim]Tanggal: {a.get('date', 'N/A')} | {a['word_count']} kata[/dim]")
                console.print(f"     [dim]{a['url']}[/dim]")

            console.print(f"\n[bold green]📁 Tersimpan di:[/bold green]")
            console.print(f"   {json_path}")
            console.print(f"   {csv_path}")
            console.print("=" * 60)
        else:
            console.print("\n[yellow]⚠ Tidak ditemukan berita tentang Solusiku[/yellow]")
            if all_articles:
                console.print(f"[dim]({len(all_articles)} artikel di-scrape tapi tidak mengandung 'Solusiku')[/dim]")

        return relevant
    def _print_summary(self):
        """Print ringkasan hasil scraping."""
        console.print("\n")
        console.print("=" * 60)
        console.print("[bold]📊 SCRAPING SUMMARY[/bold]")
        console.print("=" * 60)

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Platform", style="cyan")
        table.add_column("Tasks")
        table.add_column("Success", style="green")
        table.add_column("Failed", style="red")
        table.add_column("Items", style="yellow")

        stats = defaultdict(lambda: {"success": 0, "failed": 0, "items": 0})

        for result in self.results:
            stat = stats[result.platform]
            if result.status == TaskStatus.SUCCESS:
                stat["success"] += 1
                stat["items"] += result.count
            else:
                stat["failed"] += 1

        for platform, stat in sorted(stats.items()):
            table.add_row(
                platform,
                str(stat["success"] + stat["failed"]),
                str(stat["success"]),
                str(stat["failed"]),
                str(stat["items"]),
            )

        console.print(table)

        total_items = sum(s["items"] for s in stats.values())
        total_success = sum(s["success"] for s in stats.values())
        total_failed = sum(s["failed"] for s in stats.values())

        console.print(f"\n[bold]  Total: {total_success + total_failed} tasks | "
                      f"[green]{total_success} success[/green] | "
                      f"[red]{total_failed} failed[/red] | "
                      f"[yellow]{total_items} items scraped[/yellow][/bold]")
        console.print("=" * 60)
        console.print()