# src/core/proxy_pool.py
# """Manajemen proxy rotation."""

import random
from typing import Optional
from rich.console import Console

console = Console()


class ProxyPool:
    """
    Rotasi proxy dengan health tracking.
    Kalau proxy sering gagal, otomatis di-skip.
    """

    def __init__(self, proxies: list[dict], enabled: bool = False):
        self.enabled = enabled
        self.proxies = []
        self._failure_count: dict[str, int] = {}

        if not enabled:
            return

        for proxy in proxies:
            if isinstance(proxy, str):
                proxy = {"server": proxy}
            self.proxies.append(proxy)
            self._failure_count[proxy["server"]] = 0

    def get_next(self) -> Optional[dict]:
        """Ambil proxy berikutnya (random, tapi hindari yang sering gagal)."""
        if not self.enabled or not self.proxies:
            return None

        # Filter proxy yang terlalu sering gagal
        available = [
            p for p in self.proxies
            if self._failure_count.get(p["server"], 0) < 5
        ]

        if not available:
            console.print("[red]⚠ Semua proxy sudah di-blacklist! Reset...[/red]")
            self._failure_count = {k: 0 for k in self._failure_count}
            available = self.proxies

        return random.choice(available)

    def report_failure(self, proxy: Optional[dict]):
        """Laporkan kegagalan proxy."""
        if proxy and self.enabled:
            server = proxy.get("server", "")
            self._failure_count[server] = self._failure_count.get(server, 0) + 1

    def report_success(self, proxy: Optional[dict]):
        """Laporkan keberhasilan — reset failure count."""
        if proxy and self.enabled:
            server = proxy.get("server", "")
            self._failure_count[server] = 0

    @property
    def stats(self) -> dict:
        return {
            "total": len(self.proxies),
            "available": len([p for p in self.proxies
                            if self._failure_count.get(p["server"], 0) < 5]),
            "blacklisted": len([p for p in self.proxies
                              if self._failure_count.get(p["server"], 0) >= 5]),
        }