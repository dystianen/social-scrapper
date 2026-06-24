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
        console.print(f"[red]✗ Config file not found: {path}[/red]")
        sys.exit(1)
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


async def demo_news(orchestrator: ScrapingOrchestrator, config: dict):
    """Scrape berita dari beberapa situs Indonesia."""
    await orchestrator.run_news_search()


async def demo_instagram_comments(orchestrator: ScrapingOrchestrator, config: dict):
    post_urls = [
        "https://www.instagram.com/p/POST_ID_1/",
        "https://www.instagram.com/p/POST_ID_2/",
    ]
    tasks = [
        ScrapingTask(
            platform="instagram", url=url,
            task_type="comments",
            params={"max_comments": 30},
            priority=3,
        )
        for url in post_urls
    ]
    await orchestrator.run_tasks(tasks)
    _export_results(orchestrator, config, "instagram_comments")


async def demo_instagram_profile(orchestrator: ScrapingOrchestrator, config: dict):
    tasks = [
        ScrapingTask(
            platform="instagram",
            url="https://www.instagram.com/USERNAME/",
            task_type="profile_posts",
            params={"max_posts": 20},
            priority=3,
        )
    ]
    await orchestrator.run_tasks(tasks)
    _export_results(orchestrator, config, "instagram_profile")


async def demo_twitter(orchestrator: ScrapingOrchestrator, config: dict):
    tasks = [
        ScrapingTask(
            platform="twitter",
            url="https://x.com/USERNAME",
            task_type="profile_tweets",
            params={"max_tweets": 30},
            priority=3,
        ),
    ]
    await orchestrator.run_tasks(tasks)
    _export_results(orchestrator, config, "twitter")


async def demo_facebook(orchestrator: ScrapingOrchestrator, config: dict):
    tasks = [
        ScrapingTask(
            platform="facebook",
            url="https://www.facebook.com/PAGE_NAME",
            task_type="page_posts",
            params={"max_posts": 15},
            priority=4,
        ),
    ]
    await orchestrator.run_tasks(tasks)
    _export_results(orchestrator, config, "facebook")


def _export_results(orchestrator: ScrapingOrchestrator, config: dict, name: str):
    """Export hasil dari semua task yang sudah jalan."""
    all_data = []
    for r in orchestrator.results:
        if r.data:
            all_data.extend(r.data)

    if all_data:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = orchestrator.storage.export_json(all_data, f"{name}_{timestamp}.json")
        orchestrator.storage.export_csv(all_data, f"{name}_{timestamp}.csv")

        console.print(f"\n[bold green]📁 HASIL TERSIMPAN DI:[/bold green]")
        console.print(f"   [cyan]JSON:[/cyan] {json_path}")
        console.print(f"   [cyan]CSV :[/cyan] {config.get('general', {}).get('output_dir', './data/output')}/{name}_{timestamp}.csv")
        console.print()
    else:
        console.print("\n[yellow]⚠ Tidak ada data yang berhasil di-scrape[/yellow]")


async def interactive_mode(orchestrator: ScrapingOrchestrator, config: dict):
    console.print(Panel.fit(
        "[bold]🤖 Social Media & News Scraper[/bold]\n\n"
        "Pilih mode:\n"
        "  [cyan]1[/cyan] — Login platform (Instagram, Facebook)\n"
        "  [cyan]2[/cyan] — Scrape Instagram Comments\n"
        "  [cyan]3[/cyan] — Scrape Instagram Profile\n"
        "  [cyan]4[/cyan] — Scrape Twitter Profile\n"
        "  [cyan]5[/cyan] — Scrape Twitter Search\n"
        "  [cyan]6[/cyan] — Scrape Situs Berita\n"
        "  [cyan]7[/cyan] — Scrape Facebook Page\n"
        "  [cyan]0[/cyan] — Keluar",
        title="Menu",
        border_style="cyan",
    ))

    choice = Prompt.ask("Pilih", choices=["0", "1", "2", "3", "4", "5", "6", "7"], default="6")

    if choice == "0":
        return
    elif choice == "1":
        console.print("\n[bold]Login ke platform:[/bold]")
        console.print("  [cyan]1[/cyan] — Instagram")
        console.print("  [cyan]2[/cyan] — Facebook")
        pc = Prompt.ask("Platform", choices=["1", "2"])
        await orchestrator.login_platforms(["instagram"] if pc == "1" else ["facebook"])
    elif choice == "2":
        url = Prompt.ask("URL post Instagram")
        mc = int(Prompt.ask("Max komentar", default="50"))
        orchestrator.results = []
        await orchestrator.run_tasks([ScrapingTask(
            platform="instagram", url=url, task_type="comments",
            params={"max_comments": mc}
        )])
        _export_results(orchestrator, config, "ig_comments")
    elif choice == "3":
        username = Prompt.ask("Username Instagram")
        mp = int(Prompt.ask("Max posts", default="20"))
        orchestrator.results = []
        await orchestrator.run_tasks([ScrapingTask(
            platform="instagram",
            url=f"https://www.instagram.com/{username}/",
            task_type="profile_posts",
            params={"max_posts": mp}
        )])
        _export_results(orchestrator, config, "ig_profile")
    elif choice == "4":
        username = Prompt.ask("Username Twitter")
        mt = int(Prompt.ask("Max tweets", default="30"))
        orchestrator.results = []
        await orchestrator.run_tasks([ScrapingTask(
            platform="twitter",
            url=f"https://x.com/{username}",
            task_type="profile_tweets",
            params={"max_tweets": mt}
        )])
        _export_results(orchestrator, config, "twitter")
    elif choice == "5":
        query = Prompt.ask("Kata kunci pencarian")
        mr = int(Prompt.ask("Max hasil", default="30"))
        orchestrator.results = []
        await orchestrator.run_tasks([ScrapingTask(
            platform="twitter", url="",
            task_type="search",
            params={"query": query, "max_results": mr}
        )])
        _export_results(orchestrator, config, "twitter_search")
    elif choice == "6":
        await demo_news(orchestrator, config)
    elif choice == "7":
        page_name = Prompt.ask("Nama halaman Facebook")
        mp = int(Prompt.ask("Max posts", default="15"))
        orchestrator.results = []
        await orchestrator.run_tasks([ScrapingTask(
            platform="facebook",
            url=f"https://www.facebook.com/{page_name}",
            task_type="page_posts",
            params={"max_posts": mp}
        )])
        _export_results(orchestrator, config, "facebook")

    if Confirm.ask("\nMau scrape lagi?"):
        await interactive_mode(orchestrator, config)


async def main():
    config = load_config()

    console.print(Panel.fit(
        "[bold cyan]🤖 Social Media & News Scraper Bot[/bold cyan]\n"
        "[dim]Browser-based scraping untuk Instagram, Twitter, Facebook & Berita[/dim]",
        border_style="cyan",
    ))

    orchestrator = ScrapingOrchestrator(config)

    try:
        await orchestrator.initialize()

        if len(sys.argv) > 1:
            mode = sys.argv[1]
            if mode == "--news":
                await orchestrator.run_news_search()
            elif mode == "--instagram":
                await demo_instagram_comments(orchestrator, config)
            elif mode == "--twitter":
                await demo_twitter(orchestrator, config)
            elif mode == "--login":
                await orchestrator.login_platforms()
            else:
                console.print(f"[yellow]Unknown mode: {mode}[/yellow]")
                console.print("Usage: python -m src.main [--news|--instagram|--twitter|--login]")
        else:
            await interactive_mode(orchestrator, config)

    except KeyboardInterrupt:
        console.print("\n[yellow]Dihentikan oleh user[/yellow]")
    except Exception as e:
        console.print(f"\n[red]Fatal error: {e}[/red]")
        import traceback
        traceback.print_exc()
    finally:
        await orchestrator.shutdown()


if __name__ == "__main__":
    asyncio.run(main())