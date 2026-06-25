# src/main.py
"""Entry point utama."""

import asyncio
import yaml
import sys
from pathlib import Path
from datetime import datetime
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.panel import Panel

from src.core.models import ScrapingTask
from src.orchestrator.scheduler import ScrapingOrchestrator

console = Console()


def load_config(path: str = "config/settings.yml") -> dict:
    config_path = Path(path)
    if not config_path.exists():
        console.print(f"[red]✗ Config tidak ditemukan: {path}[/red]")
        sys.exit(1)
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ═══════════════════════════════════════
# INSTAGRAM: Login + OTP + Hashtag #solusiku
# ═══════════════════════════════════════
async def run_instagram(orchestrator, config):
    """Full flow: Login → OTP → Scrape #solusiku → Export."""

    console.print("\n" + "=" * 60)
    console.print("[bold cyan]📸 INSTAGRAM — #SOLUSIKU[/bold cyan]")
    console.print("=" * 60)

    accounts = config.get("accounts", {}).get("instagram", [])
    if not accounts:
        console.print("[red]✗ Isi akun Instagram di config/settings.yml dulu[/red]")
        return

    # ── INPUT PARAMETERS FIRST ──
    console.print("\n[bold]Step 1: Parameter Pencarian[/bold]")
    hashtag = Prompt.ask("Hashtag", default="solusiku").replace("#", "")
    max_posts = int(Prompt.ask("Berapa post", default="20"))

    # ── LOGIN ──
    console.print("\n[bold]Step 2: Login Instagram[/bold]")
    acc = accounts[0]
    ig = orchestrator.adapters["instagram"]

    success = await ig.login(acc["username"], acc["password"], acc.get("email", ""), close_session=False)
    if not success:
        console.print("[red]✗ Login gagal, stop[/red]")
        return

    # ── SCRAPE HASHTAG ──
    console.print(f"\n[bold]Step 3: Scrape hashtag #{hashtag}[/bold]")

    tasks = [ScrapingTask(
        platform="instagram",
        url=f"https://www.instagram.com/explore/tags/{hashtag}/",
        task_type="hashtag",
        params={
            "hashtag": hashtag,
            "max_posts": max_posts,
            "max_comments_per_post": config.get("limits", {}).get("instagram", {}).get("max_comments_per_post", 50),
        },
        priority=1,
    )]

    orchestrator.results = []
    await orchestrator.run_tasks(tasks)

    # ── EXPORT ──
    all_data = []
    for r in orchestrator.results:
        if r.data:
            all_data.extend(r.data)

    if all_data:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # JSON formatting matching scraped_items and comments database schema
        from src.core.keyword_filter import KeywordFilter
        kf = KeywordFilter("config/news_sources.yml")
        
        task_id = tasks[0].id if tasks else ""
        formatted_json = []
        flat_rows = []
        
        for post in all_data:
            caption = post.get("caption", "")
            
            # Hitung skor relevansi & matched keywords
            dummy = {"title": "", "content": caption}
            is_rel, relevance_score, matched_w, matched_k = kf.is_relevant(dummy)
            matched_keywords = matched_w + matched_k
            
            word_count = len(caption.split()) if caption else 0
            post_id = post.get("post_id", "")
            url = post.get("post_url", "")
            username = post.get("username", "")
            raw_date = post.get("timestamp", "")
            
            # scraped_items fields representation
            item = {
                "platform": "instagram",
                "source": "instagram",
                "item_type": "ig_post",
                "external_id": post_id,
                "url": url,
                "username": username,
                "title": None,
                "content": caption,
                "description": None,
                "author": None,
                "published_at": raw_date,
                "raw_date_str": raw_date,
                "likes": post.get("likes", 0),
                "shares": 0,
                "comments_count": post.get("comments_count", len(post.get("comments", []))),
                "word_count": word_count,
                "relevance_score": relevance_score,
                "matched_keywords": matched_keywords,
                "extra": {},
                "scraped_at": datetime.now().isoformat() + "Z",
                "task_id": task_id,
            }
            
            # comments representation
            comments_list = []
            for c in post.get("comments", []):
                c_user = c.get("username", "")
                c_text = c.get("text", "")
                c_time = c.get("timestamp", "")
                
                comment_row = {
                    "platform": "instagram",
                    "username": c_user,
                    "text": c_text,
                    "published_at": c_time,
                    "raw_date_str": c_time,
                    "likes": 0,
                    "reply_to_id": None,
                    "extra": {},
                    "scraped_at": datetime.now().isoformat() + "Z"
                }
                comments_list.append(comment_row)
                
            item["comments"] = comments_list
            formatted_json.append(item)
            
            # Flat rows for Excel/CSV export matching both schemas
            base_flat = {
                "platform": item["platform"],
                "source": item["source"],
                "item_type": item["item_type"],
                "external_id": item["external_id"],
                "url": item["url"],
                "username": item["username"],
                "title": item["title"],
                "content": item["content"],
                "description": item["description"],
                "author": item["author"],
                "published_at": item["published_at"],
                "raw_date_str": item["raw_date_str"],
                "likes": item["likes"],
                "shares": item["shares"],
                "comments_count": item["comments_count"],
                "word_count": item["word_count"],
                "relevance_score": item["relevance_score"],
                "matched_keywords": ",".join(item["matched_keywords"]) if item["matched_keywords"] else "",
            }
            
            if comments_list:
                for c in comments_list:
                    flat_rows.append({
                        **base_flat,
                        "comment_username": c["username"],
                        "comment_text": c["text"],
                        "comment_published_at": c["published_at"],
                        "comment_raw_date_str": c["raw_date_str"],
                        "comment_likes": c["likes"],
                    })
            else:
                flat_rows.append({
                    **base_flat,
                    "comment_username": None,
                    "comment_text": None,
                    "comment_published_at": None,
                    "comment_raw_date_str": None,
                    "comment_likes": None,
                })
                
        json_path = orchestrator.storage.export_json(formatted_json, f"ig_{hashtag}_{timestamp}.json")
        csv_path = orchestrator.storage.export_csv(flat_rows, f"ig_{hashtag}_{timestamp}.csv")
 
        total_posts = len(all_data)
        total_comments = sum(len(p.get("comments", [])) for p in all_data)
 
        console.print("\n" + "=" * 60)
        console.print(f"[bold green]✅ #{hashtag} — SELESAI[/bold green]")
        console.print("=" * 60)
        console.print(f"  Post        : {total_posts}")
        console.print(f"  Komentar    : {total_comments}")
        console.print(f"  Total baris : {len(flat_rows)}")
        console.print(f"\n  📁 {json_path}")
        console.print(f"  📁 {csv_path}")
        console.print("=" * 60)
    else:
        console.print("[yellow]⚠ Tidak ada data[/yellow]")


# ═══════════════════════════════════════
# NEWS: Scrape berita Solusiku
# ═══════════════════════════════════════
async def run_news(orchestrator, config):
    await orchestrator.run_news_search()


# ═══════════════════════════════════════
# INTERACTIVE MENU
# ═══════════════════════════════════════
async def interactive_mode(orchestrator, config):
    console.print(Panel.fit(
        "[bold]🤖 Solusiku Scraper[/bold]\n\n"
        "  [cyan]1[/cyan] — Login Instagram\n"
        "  [cyan]2[/cyan] — Scrape Instagram #solusiku\n"
        "  [cyan]3[/cyan] — Scrape Berita Solusiku\n"
        "  [cyan]0[/cyan] — Keluar",
        title="Menu", border_style="cyan",
    ))

    choice = Prompt.ask("Pilih", choices=["0", "1", "2", "3"], default="2")

    if choice == "0":
        return
    elif choice == "1":
        acc = config["accounts"]["instagram"][0]
        ig = orchestrator.adapters["instagram"]
        await ig.login(acc["username"], acc["password"], acc.get("email", ""))
    elif choice == "2":
        await run_instagram(orchestrator, config)
    elif choice == "3":
        await run_news(orchestrator, config)

    if Confirm.ask("\nMau lagi?"):
        await interactive_mode(orchestrator, config)


# ═══════════════════════════════════════
# MAIN
# ═══════════════════════════════════════
async def main():
    config = load_config()

    console.print(Panel.fit(
        "[bold cyan]🤖 Solusiku Scraper Bot[/bold cyan]\n"
        "[dim]Instagram Hashtag + Berita[/dim]",
        border_style="cyan",
    ))

    orchestrator = ScrapingOrchestrator(config)

    try:
        await orchestrator.initialize()

        if len(sys.argv) > 1:
            mode = sys.argv[1]
            if mode == "--instagram":
                await run_instagram(orchestrator, config)
            elif mode == "--instagram-login":
                acc = config["accounts"]["instagram"][0]
                ig = orchestrator.adapters["instagram"]
                await ig.login(acc["username"], acc["password"], acc.get("email", ""))
            elif mode == "--news":
                await orchestrator.run_news_search()
            else:
                console.print("Usage:")
                console.print("  python -m src.main              # Menu")
                console.print("  python -m src.main --instagram  # IG #solusiku")
                console.print("  python -m src.main --news       # Berita")
        else:
            await interactive_mode(orchestrator, config)

    except KeyboardInterrupt:
        console.print("\n[yellow]Dihentikan[/yellow]")
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        import traceback
        traceback.print_exc()
    finally:
        await orchestrator.shutdown()


if __name__ == "__main__":
    asyncio.run(main())