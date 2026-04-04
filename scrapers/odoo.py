"""
odoo.py — Scraper para tiendas Odoo (oe_website_sale).

Estructura estándar de Odoo e-commerce con paginación /page/N.

Tiendas cubiertas:
  LaTiendaNacional
"""

import logging
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, Product, ScrapeError
from scrapers.stores import StoreConfig

logger = logging.getLogger(__name__)


class OdooScraper(BaseScraper):
    """Scraper para tiendas Odoo (oe_website_sale)."""

    def __init__(self, store: StoreConfig):
        super().__init__(store)
        self.limit: int = 0

    def _page_url(self, page: int) -> str:
        base = self.store.vinyl_url.rstrip("/")
        if page == 1:
            return base
        # Odoo usa /page/N como sufijo de ruta
        return f"{base}/page/{page}"

    async def scrape(self) -> tuple[list[Product], Optional[ScrapeError]]:
        products: list[Product] = []
        page = 1

        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            while True:
                url = self._page_url(page)
                try:
                    resp = await self.fetch_with_timeout_retry(client, url)
                except ScrapeError as e:
                    if page == 1:
                        return [], e
                    if e.error_type in ("BLOCKED", "RATE_LIMITED"):
                        return products, e
                    break

                if resp.status_code == 404:
                    break

                soup = BeautifulSoup(resp.text, "lxml")
                items = self._find_products(soup)
                if not items:
                    if page == 1:
                        logger.warning(f"[{self.store.name}] Página 1 sin productos")
                        self.record_parse_attempt(False)
                    break

                for item in items:
                    product = self._parse_product(item)
                    if product:
                        products.append(product)
                        self.record_parse_attempt(True)
                    else:
                        self.record_parse_attempt(False)

                logger.info(f"[{self.store.name}] Página {page}: {len(items)} productos ({len(products)} total)")

                if self.limit and len(products) >= self.limit:
                    break

                if not self._has_next_page(soup):
                    break

                page += 1
                await self._delay()

        logger.info(f"[{self.store.name}] Total: {len(products)} productos en {page} páginas")
        return products, None

    def _find_products(self, soup: BeautifulSoup) -> list:
        for sel in [
            ".as-product",           # LaTiendaNacional custom theme
            ".oe_product_cart",      # Odoo estándar
            ".o_wsale_product",
            "div[itemtype*='Product']",
        ]:
            items = soup.select(sel)
            if items:
                return items
        return []

    def _parse_product(self, item) -> Optional[Product]:
        try:
            # ── Título ───────────────────────────────────────────────────
            title_el = (
                item.select_one("h3 a[itemprop='name']")
                or item.select_one("a[itemprop='name']")
                or item.select_one(".o_product_name a")
                or item.select_one("h3 strong a")
                or item.select_one("h3 a")
                or item.select_one("h4 a")
            )
            if not title_el:
                return None
            raw_title = title_el.get_text(strip=True)
            if not raw_title:
                return None

            title_norm = raw_title.replace("–", " - ").replace("—", " - ")
            if " - " in title_norm:
                parts = title_norm.split(" - ", 1)
                artist = parts[0].strip()
                album = parts[1].strip()
            else:
                artist = ""
                album = raw_title

            # ── Precio ───────────────────────────────────────────────────
            price_el = (
                item.select_one("[itemprop='price']")
                or item.select_one(".product_price .oe_price .oe_currency_value")
                or item.select_one(".oe_currency_value")
                or item.select_one("[class*=price]")
            )
            price = self.parse_price(price_el.get_text()) if price_el else 0

            # ── Disponibilidad ───────────────────────────────────────────
            # Odoo: si el producto aparece en lista, generalmente está disponible
            available = True
            out_el = item.select_one(".o_add_cart_btn, [class*=unavailable], .out-of-stock")
            if out_el:
                cls = " ".join(out_el.get("class", []))
                if "disabled" in cls or "unavailable" in cls:
                    available = False

            # ── URL ──────────────────────────────────────────────────────
            link_el = (
                item.select_one("a[href*='/shop/product/']")
                or item.select_one("a.preview-image")
                or item.select_one("a[href]")
            )
            url = ""
            if link_el and link_el.get("href"):
                href = link_el["href"]
                # Quitar parámetro ?category=N de la URL
                href = href.split("?")[0]
                url = href if href.startswith("http") else urljoin(self.store.base_url, href)

            if not album:
                return None

            return Product(
                artist=artist,
                album=album,
                price=price,
                available=available,
                url=url,
                store=self.store.name,
            )

        except Exception as exc:
            logger.debug(f"[{self.store.name}] Error parseando: {exc}")
            return None

    def _has_next_page(self, soup: BeautifulSoup) -> bool:
        return bool(
            soup.select_one(
                "a[rel='next'], .products_pager a[href*=page], "
                ".o_pager a[href*=page], li.o_next a"
            )
        )
