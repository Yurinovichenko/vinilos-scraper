"""
prestashop.py — Scraper para tiendas PrestaShop.

Maneja dos variantes de template:
  - PS 1.6 classic (ElevenStore, Disqueria12Pulgadas):
      div.product-container + h5 a.product-name + span.price + paginación ?p=N
  - PS 1.7 custom (MusicWorld, MusicLife):
      article.ajax_block_product / div.product-container + diferentes selectores

Tiendas cubiertas:
  ElevenStore, MusicWorld, MusicLife, Disqueria12Pulgadas
"""

import logging
from typing import Optional
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, Product, ScrapeError
from scrapers.stores import StoreConfig

logger = logging.getLogger(__name__)


class PrestashopScraper(BaseScraper):
    """Scraper para tiendas PrestaShop (PS 1.6 y PS 1.7)."""

    def __init__(self, store: StoreConfig):
        super().__init__(store)
        self.limit: int = 0
        self._page_param: Optional[str] = None  # Detectado en primera página

    def _page_url(self, page: int) -> str:
        base = self.store.vinyl_url
        if page == 1:
            return base
        param = self._page_param or "page"
        # Añadir parámetro de paginación a la URL base
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}{param}={page}"

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
                        logger.warning(f"[{self.store.name}] Página 1 sin productos — verificar selector")
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

                if not self._has_next_page(soup, page):
                    break

                page += 1
                await self._delay()

        logger.info(f"[{self.store.name}] Total: {len(products)} productos en {page} páginas")
        return products, None

    def _find_products(self, soup: BeautifulSoup) -> list:
        """Detecta el selector de producto correcto para la variante PrestaShop."""
        for selector in [
            "div.product-container",      # PS 1.6 (ElevenStore, Disqueria12, MusicWorld)
            "article.ajax_block_product", # PS 1.7 custom (MusicLife)
            "article.product-miniature",  # PS 1.7 standard
            ".product-miniature",
            "li.ajax_block_product",
        ]:
            items = soup.select(selector)
            if items:
                return items
        return []

    def _parse_product(self, item) -> Optional[Product]:
        """Extrae un Product desde un elemento PrestaShop."""
        try:
            # ── Título ───────────────────────────────────────────────────
            title_el = (
                item.select_one("h5 a.product-name")        # PS 1.6 standard
                or item.select_one("a.product-name")
                or item.select_one("h3 a")                  # PS 1.7 custom (MusicLife)
                or item.select_one("h4 a")
                or item.select_one("h2 a")
                or item.select_one("[itemprop='name'] a")
                or item.select_one("a.product_img_link")    # Fallback: usa atributo title
            )
            if not title_el:
                return None

            # Preferir el atributo title del link (más limpio que el texto)
            raw_title = title_el.get("title") or title_el.get_text(strip=True)
            # Limpiar prefijos de tienda (ej: "VINILOS - MUSICLIFE | ...")
            if " | " in raw_title:
                raw_title = raw_title.split(" | ", 1)[-1].strip()

            if not raw_title:
                return None

            # Separar artista / álbum
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
                item.select_one("span.price.product-price")  # PS 1.6
                or item.select_one("span[itemprop='price']")
                or item.select_one(".current-price-value")   # PS 1.7
                or item.select_one(".product-price")
                or item.select_one(".price")
            )
            price = self.parse_price(price_el.get_text()) if price_el else 0

            # ── Disponibilidad ───────────────────────────────────────────
            available = True
            stock_el = item.select_one(
                ".availability, .label-out-of-stock, .product-unavailable, "
                ".out-of-stock, [class*='unavailable']"
            )
            if stock_el:
                txt = stock_el.get_text(strip=True).lower()
                if any(w in txt for w in ("agotado", "sin stock", "out of stock", "unavailable", "no disponible")):
                    available = False
                elif "en stock" in txt or "disponible" in txt or "in stock" in txt:
                    available = True

            # Si no hay botón de compra → probablemente sin stock
            add_btn = item.select_one("a.ajax_add_to_cart_button, button[data-button-action='add-to-cart']")
            if add_btn:
                if "disabled" in " ".join(add_btn.get("class", [])):
                    available = False

            # ── URL del producto ─────────────────────────────────────────
            link_el = (
                item.select_one("a.product_img_link[href]")
                or item.select_one("h5 a[href]")
                or item.select_one("h3 a[href]")
                or item.select_one("a[itemprop='url']")
                or item.select_one("a[href]")
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
            logger.debug(f"[{self.store.name}] Error parseando producto: {exc}")
            return None

    def _has_next_page(self, soup: BeautifulSoup, current_page: int) -> bool:
        """Detecta si existe una página siguiente en PrestaShop."""
        # Estándar: rel=next en elemento a
        next_link = soup.select_one(
            "a[rel='next'], .pagination a.next, .pagination li.next a"
        )
        if next_link:
            href = next_link.get("href", "")
            # Detectar el parámetro de paginación usado (p= o page=)
            if "?p=" in href or "&p=" in href:
                self._page_param = "p"
            elif "?page=" in href or "&page=" in href:
                self._page_param = "page"
            return True

        # Alternativa: buscar número de página siguiente en paginación
        for sel in [".pagination a", "ul.pagination a", "#pagination a"]:
            for link in soup.select(sel):
                txt = link.get_text(strip=True)
                href = link.get("href", "")
                if txt == str(current_page + 1):
                    return True
                if f"p={current_page + 1}" in href or f"page={current_page + 1}" in href:
                    return True

        return False
