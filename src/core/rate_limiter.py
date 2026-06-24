# src/core/rate_limiter.py
# """Rate limiter untuk membatasi jumlah request."""

import asyncio
import time
from collections import deque
from rich.console import Console

console = Console()


class RateLimiter:
    """
    Token bucket rate limiter.
    Memastikan bot tidak terlalu agresif dan terdeteksi.
    """

    def __init__(self, max_requests: int = 30, per_seconds: float = 60.0, name: str = "default"):
        self.max_requests = max_requests
        self.per_seconds = per_seconds
        self.name = name
        self.timestamps: deque = deque()
        self._lock = asyncio.Lock()

    async def wait(self):
        """Tunggu sampai request berikutnya diizinkan."""
        async with self._lock:
            now = time.monotonic()

            # Hapus timestamp yang sudah expired
            while self.timestamps and self.timestamps[0] <= now - self.per_seconds:
                self.timestamps.popleft()

            # Kalau masih ada slot, langsung izinkan
            if len(self.timestamps) < self.max_requests:
                self.timestamps.append(now)
                return

            # Kalau penuh, hitung berapa lama harus tunggu
            wait_time = self.per_seconds - (now - self.timestamps[0])
            console.print(
                f"  [yellow]⏳ Rate limit [{self.name}]: tunggu {wait_time:.1f}s[/yellow]"
            )
            await asyncio.sleep(wait_time)

            # Setelah tunggu, baru izinkan
            self.timestamps.popleft()
            self.timestamps.append(time.monotonic())

    def get_remaining(self) -> int:
        """Berapa request yang masih diizinkan."""
        now = time.monotonic()
        while self.timestamps and self.timestamps[0] <= now - self.per_seconds:
            self.timestamps.popleft()
        return max(0, self.max_requests - len(self.timestamps))