# src/adapters/base.py
"""Base class untuk semua platform adapter."""

import asyncio
import random
from abc import ABC, abstractmethod
from typing import Optional
from datetime import datetime
from rich.console import Console

from src.core.models import ScrapingTask, ScrapingResult, TaskStatus
from src.core.browser_manager import BrowserManager
from src.core.human_simulator import HumanSimulator
from src.core.rate_limiter import RateLimiter
from src.core.proxy_pool import ProxyPool
from src.core.dedup import Deduplication
from src.core.storage import Storage

console = Console()

# Task types yang TIDAK boleh di-dedup by URL
# karena data konten selalu unik meskipun URL sama
SKIP_DEDUP_FOR_TASK_TYPES = {"article", "comments", "tweet_replies", "post_comments", "hashtag", "profile_posts"}


class BaseAdapter(ABC):
    platform_name: str = "base"

    def __init__(
        self,
        browser_manager: BrowserManager,
        rate_limiter: RateLimiter,
        proxy_pool: ProxyPool,
        dedup: Deduplication,
        storage: Storage,
        config: dict,
    ):
        self.browser = browser_manager
        self.human = HumanSimulator()
        self.rate_limiter = rate_limiter
        self.proxy_pool = proxy_pool
        self.dedup = dedup
        self.storage = storage
        self.config = config
        self.limits = config.get("limits", {}).get(self.platform_name, {})

    @abstractmethod
    async def scrape(self, task: ScrapingTask) -> ScrapingResult:
        ...

    async def execute(self, task: ScrapingTask) -> ScrapingResult:
        task.status = TaskStatus.RUNNING
        console.print(f"\n[cyan]▶ [{self.platform_name}] {task.task_type}: {task.url or '(from config)'}[/cyan]")

        await self.rate_limiter.wait()
        proxy = self.proxy_pool.get_next()

        try:
            result = await self.scrape(task)

            # ── FIX: hanya dedup untuk task types yang relevan ──
            if result.data and task.task_type not in SKIP_DEDUP_FOR_TASK_TYPES:
                before = len(result.data)
                result.data = self.dedup.filter_new(result.data, key_field="url")
                after = len(result.data)
                if before != after:
                    console.print(f"  [dim]Dedup: {before} → {after} items[/dim]")
            elif result.data and task.task_type in SKIP_DEDUP_FOR_TASK_TYPES:
                console.print(f"  [dim]Dedup skipped for task type: {task.task_type}[/dim]")

            await self.storage.save_result(
                platform=self.platform_name,
                task_id=task.id,
                task_type=task.task_type,
                url=task.url,
                data=result.data,
            )

            self.proxy_pool.report_success(proxy)
            task.status = TaskStatus.SUCCESS
            console.print(f"  [green]✓ {result.count} items scraped[/green]")
            return result

        except Exception as e:
            task.status = TaskStatus.FAILED
            error_msg = str(e)
            console.print(f"  [red]✗ Error: {error_msg}[/red]")

            self.proxy_pool.report_failure(proxy)

            await self.storage.save_result(
                platform=self.platform_name,
                task_id=task.id,
                task_type=task.task_type,
                url=task.url,
                data=[],
                status="failed",
                error=error_msg,
            )

            return ScrapingResult(
                task_id=task.id,
                platform=self.platform_name,
                data=[],
                status=TaskStatus.FAILED,
                error=error_msg,
            )

    def _check_session_limit(self, action_count: int) -> bool:
        max_per_session = self.limits.get("max_posts_per_session",
                         self.limits.get("max_profiles_per_session",
                         self.limits.get("max_pages_per_session", 50)))
        return action_count < max_per_session