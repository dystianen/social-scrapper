# src/core/dedup.py
"""Dedup supaya data yang sama tidak di-scrape dua kali."""

import hashlib
import json
from pathlib import Path
from typing import Optional
from rich.console import Console

console = Console()


class Deduplication:
    """
    Mencegah duplikasi data.
    Pakai hash dari URL atau konten untuk deteksi.
    """

    def __init__(self, db_path: Optional[str] = None):
        self._seen: set[str] = set()
        self._db_path = Path(db_path) if db_path else None

        # Load dari file kalau ada
        if self._db_path and self._db_path.exists():
            self._seen = set(self._db_path.read_text().strip().split("\n"))
            console.print(f"[dim]Dedup: loaded {len(self._seen)} hashes[/dim]")

    def _hash(self, data) -> str:
        """Buat hash dari data."""
        if isinstance(data, dict):
            data = json.dumps(data, sort_keys=True, default=str)
        elif not isinstance(data, str):
            data = str(data)
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    def is_duplicate(self, data, key_field: Optional[str] = None) -> bool:
        """
        Cek apakah data sudah pernah ada.
        key_field: field spesifik yang jadi identifier (misal "url" atau "post_id").
        """
        if key_field and isinstance(data, dict):
            hash_val = self._hash(data.get(key_field, ""))
        else:
            hash_val = self._hash(data)
        return hash_val in self._seen

    def mark_seen(self, data, key_field: Optional[str] = None):
        """Tandai data sebagai sudah dilihat."""
        if key_field and isinstance(data, dict):
            hash_val = self._hash(data.get(key_field, ""))
        else:
            hash_val = self._hash(data)
        self._seen.add(hash_val)

    def filter_new(self, items: list[dict], key_field: Optional[str] = None) -> list[dict]:
        """Filter list, hanya return yang belum pernah dilihat."""
        new_items = []
        for item in items:
            if not self.is_duplicate(item, key_field):
                self.mark_seen(item, key_field)
                new_items.append(item)
        return new_items

    def save(self):
        """Simpan hash ke file."""
        if self._db_path:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db_path.write_text("\n".join(self._seen))

    @property
    def count(self) -> int:
        return len(self._seen)