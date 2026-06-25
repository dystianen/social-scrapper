# src/core/storage.py
"""Penyimpanan data — SQLite, PostgreSQL, JSON, CSV."""
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
    Supports SQLite for internal caching, PostgreSQL for primary storage, and exports to JSON/CSV.
    """

    def __init__(self, config: dict):
        self.config = config
        self.db_path = Path(config.get("db_path", "./data/cache/scraper.db"))
        self.output_dir = Path(config.get("output_dir", "./data/output"))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.db_type = config.get("type", "sqlite")
        self.pg_config = config.get("postgresql", {})
        
        self._db: Optional[aiosqlite.Connection] = None  # SQLite connection
        self._pg_conn = None  # PostgreSQL connection (psycopg2)

    async def initialize(self):
        """Buat database dan tabel."""
        # Selalu initialize SQLite cache/dedup
        self._db = await aiosqlite.connect(str(self.db_path))

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
        console.print("[green]✓ SQLite storage initialized[/green]")

        # PostgreSQL Connection
        if self.db_type == "postgresql":
            try:
                import psycopg2
                
                loop = asyncio.get_event_loop()
                self._pg_conn = await loop.run_in_executor(
                    None,
                    lambda: psycopg2.connect(
                        host=self.pg_config.get("host", "localhost"),
                        port=self.pg_config.get("port", 5432),
                        database=self.pg_config.get("database", "social_sentiment"),
                        user=self.pg_config.get("user", "postgres"),
                        password=self.pg_config.get("password", "")
                    )
                )
                self._pg_conn.autocommit = True
                console.print("[green]✓ PostgreSQL storage connected[/green]")
            except Exception as e:
                console.print(f"[red]✗ PostgreSQL connection failed: {e}[/red]")
                console.print("[yellow]⚠ Fallback ke SQLite cache[/yellow]")
                self.db_type = "sqlite"

    async def close(self):
        if self._db:
            await self._db.close()
            self._db = None
        if self._pg_conn:
            try:
                self._pg_conn.close()
            except Exception:
                pass
            self._pg_conn = None

    async def save_result(self, platform: str, task_id: str, task_type: str,
                          url: str, data: list[dict], status: str = "success",
                          error: Optional[str] = None):
        """Simpan hasil scraping ke database."""
        if not self._db:
            console.print("[yellow]⚠ Storage not initialized, skipping save[/yellow]")
            return

        # ── SQLite Cache (Internal) ──
        await self._db.execute(
            """INSERT INTO scraping_results
               (platform, task_id, task_type, url, data_json, data_count, status, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (platform, task_id, task_type, url,
             json.dumps(data, default=str, ensure_ascii=False),
             len(data), status, error)
        )
        await self._db.commit()

        # ── PostgreSQL Save (jika aktif) ──
        if self.db_type == "postgresql" and self._pg_conn:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._save_pg, platform, task_id, task_type, data)

    def _save_pg(self, platform: str, task_id: str, task_type: str, data: list[dict]):
        if not self._pg_conn:
            return

        cursor = self._pg_conn.cursor()

        items_count = 0
        comments_count_total = 0

        # Load keywords filter
        from src.core.keyword_filter import KeywordFilter
        kf = KeywordFilter("config/news_sources.yml")

        for record in data:
            try:
                # Normalisasi link item/post
                item_url = record.get("url") or record.get("post_url") or ""
                if not item_url:
                    continue

                if platform == "instagram":
                    item_type = "ig_post"
                    source = "instagram"
                    username = record.get("username") or record.get("post_username") or ""
                    title = None
                    content = record.get("caption") or record.get("content") or ""
                    description = None
                    author = None
                    raw_date = record.get("timestamp") or record.get("raw_date_str") or ""
                    external_id = record.get("post_id") or record.get("external_id") or ""
                    if not external_id and "/p/" in item_url:
                        external_id = item_url.split("/p/")[1].split("/")[0]
                    elif not external_id and "/reel/" in item_url:
                        external_id = item_url.split("/reel/")[1].split("/")[0]
                elif platform == "news":
                    item_type = "article"
                    source = record.get("source", "news")
                    username = None
                    title = record.get("title", "")
                    content = record.get("content", "")
                    description = record.get("description", "")
                    author = record.get("author", "")
                    raw_date = record.get("date") or record.get("raw_date_str") or ""
                    external_id = None
                else:
                    item_type = "tweet" if platform == "twitter" else "post"
                    source = platform
                    username = record.get("username", "")
                    title = record.get("title")
                    content = record.get("content") or record.get("text") or ""
                    description = None
                    author = None
                    raw_date = record.get("timestamp") or record.get("date") or ""
                    external_id = record.get("id") or record.get("tweet_id") or ""

                # Date format
                published_at = None
                if raw_date:
                    published_at = raw_date

                # Word count & relevance
                word_count = len(content.split()) if content else 0
                dummy = {"title": title or "", "content": content}
                is_rel, relevance_score, matched_wajib, matched_konteks = kf.is_relevant(dummy)
                matched_keywords = matched_wajib + matched_konteks

                comments_count = len(record.get("comments", [])) if platform == "instagram" else int(record.get("comments_count", 0))
                likes = int(record.get("likes", 0))
                shares = int(record.get("shares", 0))

                insert_item_query = """
                    INSERT INTO scraped_items (
                        platform, source, item_type, external_id, url, username, title, content, 
                        description, author, published_at, raw_date_str, likes, shares, 
                        comments_count, word_count, relevance_score, matched_keywords, extra, task_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (platform, url) DO UPDATE SET
                        content = EXCLUDED.content,
                        comments_count = EXCLUDED.comments_count,
                        likes = EXCLUDED.likes,
                        shares = EXCLUDED.shares,
                        relevance_score = EXCLUDED.relevance_score,
                        matched_keywords = EXCLUDED.matched_keywords,
                        extra = EXCLUDED.extra,
                        task_id = EXCLUDED.task_id
                    RETURNING id;
                """

                cursor.execute(insert_item_query, (
                    platform, source, item_type, external_id, item_url, username, title, content,
                    description, author, published_at, raw_date, likes, shares,
                    comments_count, word_count, relevance_score, matched_keywords, json.dumps(record.get("extra", {})), task_id
                ))

                res = cursor.fetchone()
                item_db_id = res[0] if res else None
                items_count += 1

                # Simpan komentar jika ada
                comments = record.get("comments", [])
                if item_db_id and comments:
                    cursor.execute("DELETE FROM comments WHERE item_id = %s", (item_db_id,))

                    insert_comment_query = """
                        INSERT INTO comments (
                            item_id, platform, username, text, published_at, raw_date_str, likes, reply_to_id, extra
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    for c in comments:
                        c_user = c.get("username", "")
                        c_text = c.get("text", "")
                        c_time = c.get("timestamp", "") or c.get("published_at", "")
                        c_likes = int(c.get("likes", 0))

                        cursor.execute(insert_comment_query, (
                            item_db_id, platform, c_user, c_text, c_time, c_time, c_likes, None, json.dumps(c.get("extra", {}))
                        ))
                        comments_count_total += 1

            except Exception as e:
                console.print(f"  [yellow]Warning: Gagal menyimpan item ke PostgreSQL: {e}[/yellow]")
                continue

        # Log scraping run
        try:
            insert_run_query = """
                INSERT INTO scraping_runs (
                    platform, task_type, source, status, items_scraped, comments_scraped, error_message, started_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """
            cursor.execute(insert_run_query, (
                platform, task_type, platform, "success", items_count, comments_count_total, None, datetime.utcnow().isoformat() + "Z"
            ))
        except Exception:
            pass

        cursor.close()

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
        
        # Cari semua fieldnames secara dinamis agar tidak crash jika beda keys
        all_keys = []
        for item in flat_data:
            for k in item.keys():
                if k not in all_keys:
                    all_keys.append(k)

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_keys)
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
            row[0]: {"runs": row[1], "items": row[2]}
            for row in rows
        }