"""
bsale.py — Scraper para tiendas Bsale (plataforma chilena).

Las tiendas Bsale renderizan HTML con productos en div.bs-collection__product.
Paginación: ?page=N

Tiendas cubiertas:
  Billboard, PlazaMusica
"""

import logging
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, Product, ScrapeError
from scrapers.stores import StoreConfig

logger = logging.getLogger(__name__)


class BsaleScraper(BaseScraper):
    """Scraper para tiendas Bsale."""

    def __init__(self, store: StoreConfig):
        super().__init__(store)
        self.limit: int = 0

    def _page_url(self, page: int) -> str:
        base = self.store.vinyl_url.rstrip("/")
        if page == 1:
            return base
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}page={page}"

    async def scrape(self) -> tuple[list[Product], Optional[ScrapeError]]:
        products: list[Product] = []
        page = 1

        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
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
                items = soup.select("div.bs-collection__product")
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

    def _parse_product(self, item) -> Optional[Product]:
        try:
            # ── Título ───────────────────────────────────────────────────
            # El atributo title puede estar en el <a> directo o en un <a> padre
            # PlazaMusica: <a class="bs-collection__product__img" title="...">
            # Billboard:   <a title="..."><div class="bs-collection__product__img">
            img_link = (
                item.select_one("a.bs-collection__product__img")
                or item.select_one("a[href*='/product/']")
                or item.select_one("a[href]")
            )
            title_h3 = item.select_one("h3.bs-collection__product-title a, h3 a")

            raw_title = ""
            if img_link and img_link.get("title"):
                raw_title = img_link["title"].strip()
            elif title_h3:
                raw_title = title_h3.get_text(strip=True)

            if not raw_title:
                return None

            # Quitar prefijo "VINILO " si viene en el título
            raw_title = raw_title.removeprefix("VINILO ").removeprefix("Vinilo ").strip()

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
                item.select_one(".bs-collection__product-final-price")
                or item.select_one(".bs-collection__product-price .current")
                or item.select_one("[class*=price]")
            )
            price = self.parse_price(price_el.get_text()) if price_el else 0
            if price == 0:
                return None

            # ── Disponibilidad ───────────────────────────────────────────
            # Si hay botón "agregar al carro", está disponible
            add_btn = item.select_one("button[data-bs*='cart.add'], button[class*='btn-secondary']")
            out_notice = item.select_one(".bs-collection__product-notice")
            notice_txt = out_notice.get_text(strip=True).lower() if out_notice else ""

            available = bool(add_btn)
            if "agotado" in notice_txt or "sin stock" in notice_txt:
                available = False

            # ── URL ──────────────────────────────────────────────────────
            link_el = (
                item.select_one("a.bs-collection__product__img")
                or item.select_one("a[href*='/product/']")
                or img_link
                or title_h3
            )
            url = ""
            if link_el and link_el.get("href"):
                href = link_el["href"]
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
        return bool(soup.select_one("a[rel='next'], .pagination a.next, [class*=pagination] a.next"))
