# src/core/human_simulator.py
"""Simulasi perilaku manusia di browser."""

import asyncio
import random
from playwright.async_api import Page
from rich.console import Console

console = Console()


class HumanSimulator:
    """
    Membuat bot berperilaku seperti manusia.
    Scroll, klik, ketik, dan jeda dengan pola yang natural.
    """

    # Kecepatan scroll yang bervariasi
    SCROLL_SPEEDS = ["slow", "normal", "fast"]

    async def human_delay(self, min_s: float = 0.5, max_s: float = 2.0):
        """Jeda acak seperti manusia."""
        delay = random.uniform(min_s, max_s)
        await asyncio.sleep(delay)

    async def reading_pause(self):
        """Jeda baca — lebih lama, seolah-olah membaca konten."""
        await asyncio.sleep(random.uniform(2.0, 8.0))

    async def thinking_pause(self):
        """Jeda mikir — kadang manusia berhenti sebentar."""
        await asyncio.sleep(random.uniform(1.0, 4.0))

    async def scroll_page(self, page: Page, times: int = 3):
        """
        Scroll dengan kecepatan tidak konsisten.
        Kadang scroll jauh, kadang dekat, kadang balik ke atas.
        """
        for i in range(times):
            # Jarak scroll bervariasi
            distance = random.randint(200, 600)

            # Scroll ke bawah
            await page.evaluate(f"window.scrollBy(0, {distance})")
            await self.human_delay(0.8, 3.0)

            # 20% kemungkinan scroll balik ke atas (manusia suka overshoot)
            if random.random() < 0.2:
                back_distance = random.randint(50, 200)
                await page.evaluate(f"window.scrollBy(0, -{back_distance})")
                await self.human_delay(0.3, 1.0)

            # 10% kemungkinan jeda lama (lagi baca)
            if random.random() < 0.1:
                await self.reading_pause()

    async def click_element(self, page: Page, selector: str, timeout: int = 5000):
        """
        Klik elemen dengan cara yang natural.
        Mouse bergerak ke target, kadang sedikit meleset, lalu klik.
        """
        element = page.locator(selector).first
        await element.wait_for(state="visible", timeout=timeout)
        box = await element.bounding_box()

        if not box:
            # Fallback: klik langsung
            await element.click()
            return

        # Target klik: tidak tepat di tengah
        target_x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
        target_y = box["y"] + box["height"] * random.uniform(0.3, 0.7)

        # Gerakkan mouse dulu ke area sekitar (bukan teleport langsung ke target)
        intermediate_x = target_x + random.randint(-100, 100)
        intermediate_y = target_y + random.randint(-100, 100)
        await page.mouse.move(intermediate_x, intermediate_y, steps=random.randint(3, 8))
        await self.human_delay(0.05, 0.15)

        # Gerakkan ke target sebenarnya
        await page.mouse.move(target_x, target_y, steps=random.randint(3, 10))
        await self.human_delay(0.05, 0.3)

        # Klik
        await page.mouse.click(target_x, target_y)
        await self.human_delay(0.3, 1.0)

    async def type_text(self, page: Page, selector: str, text: str):
        """
        Ketik teks dengan kecepatan bervariasi.
        Setiap karakter punya delay berbeda.
        """
        await self.click_element(page, selector)
        await self.human_delay(0.3, 0.8)

        for i, char in enumerate(text):
            await page.keyboard.press(char)

            # Kecepatan ketik normal
            delay = random.uniform(0.04, 0.12)

            # Kadang jeda lebih lama (manusia sedang mikir huruf berikutnya)
            if random.random() < 0.05:
                delay = random.uniform(0.3, 1.0)

            # Kadang jeda di tengah kata (manusia mikir kata berikutnya)
            if char == " " and random.random() < 0.3:
                delay = random.uniform(0.2, 0.6)

            await asyncio.sleep(delay)

    async def random_mouse_movement(self, page: Page):
        """Gerakkan mouse ke posisi random — seolah-olah user sedang lihat-lihat."""
        x = random.randint(100, 900)
        y = random.randint(100, 600)
        await page.mouse.move(x, y, steps=random.randint(5, 15))

    async def simulate_idle_activity(self, page: Page):
        """
        Aktivitas random saat idle.
        Membuat session terlihat natural, bukan bot yang langsung cabut.
        """
        actions = []

        # Pilih 0-3 aktivitas random
        num_actions = random.randint(0, 3)

        possible = [
            self._do_random_mouse_move,
            self._do_random_scroll_up,
            self._do_random_pause,
            self._do_random_tab_behavior,
        ]

        chosen = random.sample(possible, k=min(num_actions, len(possible)))
        for action in chosen:
            await action(page)

    async def _do_random_mouse_move(self, page: Page):
        await self.random_mouse_movement(page)

    async def _do_random_scroll_up(self, page: Page):
        distance = random.randint(50, 300)
        await page.evaluate(f"window.scrollBy(0, -{distance})")
        await self.human_delay(0.3, 1.0)

    async def _do_random_pause(self, page: Page):
        await self.reading_pause()

    async def _do_random_tab_behavior(self, page: Page):
        """Kadang manusia hover di elemen tertentu."""
        try:
            # Hover di elemen random
            elements = await page.query_selector_all("a, button, img")
            if elements and len(elements) > 0:
                random_el = random.choice(elements[:20])  # Ambil dari 20 elemen pertama
                await random_el.hover()
                await self.human_delay(0.5, 1.5)
        except Exception:
            pass