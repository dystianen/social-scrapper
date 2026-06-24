# src/core/storage.py
"""Penyimpanan data — SQLite, JSON, CSV."""
import json
import csv
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional
import aiosqlite
from rich.console import Console

console = Console()


class Storage:
    """
    Unified storage engine.
    Support SQLite untuk internal, dan export ke JSON/CSV.
    """

    def __init__(self, config: dict):
        self.config = config
        self.db_path = Path(config.get("db_path", "./data/cache/scraper.db"))
        self.output_dir = Path(config.get("output_dir", "./data/output"))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db: Optional[aiosqlite.Connection] = None  # ← init di sini

    async def initialize(self):
        """Buat database dan tabel."""
        self._db = await aiosqlite.connect(str(self.db_path))  # ← FIX: db_path bukan _db_path

        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS scraping_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                task_id TEXT NOT NULL,
                task_type TEXT NOT NULL,
                url TEXT NOT NULL,
                data_json TEXT NOT NULL,
                data_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'success',
                error_message TEXT,
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_platform ON scraping_results(platform);
            CREATE INDEX IF NOT EXISTS idx_url ON scraping_results(url);
            CREATE INDEX IF NOT EXISTS idx_scraped_at ON scraping_results(scraped_at);

            CREATE TABLE IF NOT EXISTS dedup_hashes (
                hash TEXT PRIMARY KEY,
                platform TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await self._db.commit()
        console.print("[green]✓ Storage initialized (SQLite)[/green]")

    async def close(self):
        if self._db:
            await self._db.close()
            self._db = None

    async def save_result(self, platform: str, task_id: str, task_type: str,
                          url: str, data: list[dict], status: str = "success",
                          error: Optional[str] = None):
        """Simpan hasil scraping ke database."""
        if not self._db:
            console.print("[yellow]⚠ Storage not initialized, skipping save[/yellow]")
            return

        await self._db.execute(
            """INSERT INTO scraping_results
               (platform, task_id, task_type, url, data_json, data_count, status, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (platform, task_id, task_type, url,
             json.dumps(data, default=str, ensure_ascii=False),
             len(data), status, error)
        )
        await self._db.commit()

    async def get_results(self, platform: Optional[str] = None,
                          limit: int = 100) -> list[dict]:
        """Ambil hasil scraping dari database."""
        if not self._db:
            return []

        if platform:
            cursor = await self._db.execute(
                "SELECT * FROM scraping_results WHERE platform = ? ORDER BY scraped_at DESC LIMIT ?",
                (platform, limit)
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM scraping_results ORDER BY scraped_at DESC LIMIT ?",
                (limit,)
            )
        columns = [desc[0] for desc in cursor.description]
        rows = await cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]

    def export_json(self, data: list[dict], filename: str) -> str:
        """Export data ke file JSON."""
        path = self.output_dir / filename
        path.write_text(
            json.dumps(data, default=str, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        console.print(f"[green]✓ Exported {len(data)} items → {path}[/green]")
        return str(path)

    def export_csv(self, data: list[dict], filename: str) -> str:
        """Export data ke file CSV."""
        if not data:
            console.print("[yellow]Tidak ada data untuk di-export[/yellow]")
            return ""

        # Flatten nested dicts untuk CSV
        flat_data = []
        for item in data:
            flat = {}
            for key, value in item.items():
                if isinstance(value, (list, dict)):
                    flat[key] = json.dumps(value, default=str, ensure_ascii=False)
                else:
                    flat[key] = value
            flat_data.append(flat)

        path = self.output_dir / filename
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=flat_data[0].keys())
            writer.writeheader()
            writer.writerows(flat_data)

        console.print(f"[green]✓ Exported {len(flat_data)} items → {path}[/green]")
        return str(path)

    async def get_stats(self) -> dict:
        """Statistik penyimpanan."""
        if not self._db:
            return {}

        cursor = await self._db.execute("""
            SELECT platform, COUNT(*), SUM(data_count)
            FROM scraping_results
            GROUP BY platform
        """)
        rows = await cursor.fetchall()
        return {
            row[0]: {"tasks": row[1], "total_items": row[2]}
            for row in rows
        }