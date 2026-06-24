# src/core/keyword_filter.py
"""
Filter: WAJIB mengandung "Solusiku".
Kata konteks hanya untuk penilaian relevansi (skor).
"""

import yaml
from pathlib import Path
from rich.console import Console

console = Console()


class KeywordFilter:

    def __init__(self, config_path: str = "config/news_sources.yml"):
        self.wajib: list[str] = []       # HARUS ada
        self.konteks: list[str] = []     # Opsional, untuk skor
        self._load(config_path)

    def _load(self, path: str):
        config = Path(path)
        if not config.exists():
            console.print("[yellow]⚠ Config tidak ditemukan[/yellow]")
            return

        with open(config, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        kw = data.get("keywords", {})
        self.wajib = [k.strip().lower() for k in kw.get("wajib", []) if k.strip()]
        self.konteks = [k.strip().lower() for k in kw.get("konteks", []) if k.strip()]

        console.print(f"[dim]Keyword wajib : {self.wajib}[/dim]")
        console.print(f"[dim]Keyword konteks: {len(self.konteks)} kata[/dim]")

    def is_relevant(self, article: dict) -> tuple[bool, int, list[str], list[str]]:
        """
        Cek apakah artikel relevan.

        Rules:
        - WAJIB mengandung minimal 1 kata dari 'wajib'
        - Skor = jumlah kata konteks yang match (makin tinggi makin relevan)

        Returns:
            (is_relevant, score, matched_wajib, matched_konteks)
        """
        text = (
            article.get("title", "") + " " +
            article.get("content", "")[:3000]
        ).lower()

        # STEP 1: Cek kata WAJIB
        matched_wajib = []
        for kw in self.wajib:
            if kw in text:
                matched_wajib.append(kw)

        # Kalau tidak ada kata wajib → LANGSUNG tolak
        if not matched_wajib:
            return False, 0, [], []

        # STEP 2: Hitung skor dari kata konteks
        matched_konteks = []
        for kw in self.konteks:
            if kw in text:
                matched_konteks.append(kw)

        score = len(matched_wajib) * 10 + len(matched_konteks)

        return True, score, matched_wajib, matched_konteks

    def filter_articles(self, articles: list[dict]) -> list[dict]:
        """
        Filter: hanya artikel yang mengandung 'Solusiku' (atau variasi).
        Sort berdasarkan skor relevansi.
        """
        if not self.wajib:
            console.print("[yellow]⚠ Tidak ada keyword wajib, semua diterima[/yellow]")
            return articles

        relevant = []
        rejected = 0

        for article in articles:
            is_rel, score, matched_w, matched_k = self.is_relevant(article)

            if is_rel:
                article["relevance_score"] = score
                article["matched_wajib"] = matched_w
                article["matched_konteks"] = matched_k
                relevant.append(article)
            else:
                rejected += 1

        # Sort: skor tertinggi di atas
        relevant.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)

        console.print(f"  [green]Filter: {len(relevant)} relevan (mengandung 'Solusiku'), "
                       f"{rejected} ditolak[/green]")

        return relevant