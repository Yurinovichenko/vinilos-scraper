"""
jumpseller.py — Scraper para tiendas Jumpseller (server-side HTML, sin Playwright).

Paginación: ?page=N
Selectores: div.product-block (FuturoPrimitivo, DisqueriaBazarDeCulto)
            div.product-slide-entry (SVinilos)

Tiendas cubiertas:
  SVinilos, FuturoPrimitivo, DisqueriaBazarDeCulto
"""

import logging
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, Product, ScrapeError
from scrapers.stores import StoreConfig

logger = logging.getLogger(__name__)


class JumpsellerScraper(BaseScraper):
    """Scraper para tiendas Jumpseller (HTML server-side)."""

    def __init__(self, store: StoreConfig):
        super().__init__(store)
        self.limit: int = 0

    def _page_url(self, page: int) -> str:
        base = self.store.vinyl_url.rstrip("/")
        if page == 1:
            return base + ("/" if not base.endswith(self.store.base_url) else "")
        # Jumpseller usa ?page=N
        sep = "?" if "?" not in base else "&"
        return f"{base}{sep}page={page}"

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
        """Detecta el selector de producto correcto para la plantilla Jumpseller."""
        for selector in [
            "div.product-block",        # FuturoPrimitivo, DisqueriaBazarDeCulto
            "div.product-slide-entry",  # SVinilos
            "div.product-item",
            "li.product-item",
        ]:
            items = soup.select(selector)
            if items:
                return items
        return []

    def _parse_product(self, item) -> Optional[Product]:
        """Extrae un Product desde un elemento Jumpseller."""
        try:
            # ── Título ───────────────────────────────────────────────────
            title_el = (
                item.select_one("h4 a")          # product-block estándar
                or item.select_one("h3 a.title")  # product-slide-entry (SVinilos)
                or item.select_one("h3 a")
                or item.select_one("h2 a")
                or item.select_one("img[alt]")    # fallback: alt del img
            )
            if not title_el:
                return None

            if title_el.name == "img":
                raw_title = title_el.get("alt", "").strip()
            else:
                raw_title = title_el.get_text(strip=True)

            if not raw_title:
                return None

            # Separar artista / álbum (varios formatos: " – ", " - ", ": ")
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
                item.select_one("span.product-block-list")  # FuturoPrimitivo, BazarDeCulto
                or item.select_one("div.current")           # SVinilos
                or item.select_one("[class*=price]")
            )
            price = self.parse_price(price_el.get_text()) if price_el else 0
            if price == 0:
                return None  # Sin precio = banner/promo, no es un disco real

            # ── Disponibilidad ───────────────────────────────────────────
            # "Agotado" aparece como status-tag o product-image-label
            stock_el = item.select_one(".status-tag, .product-image-label, .stock-label")
            available = True
            if stock_el:
                txt = stock_el.get_text(strip=True).lower()
                if "agotado" in txt or "sin stock" in txt or "out of stock" in txt:
                    available = False

            # Si no hay form de compra, probablemente agotado
            form = item.select_one("form[action*='/cart/add']")
            if not form:
                # Podría ser agotado o solo disponible en página de detalle
                pass  # No cambiamos available por esto solo

            # ── URL del producto ─────────────────────────────────────────
            link_el = (
                item.select_one("a.product-image")
                or item.select_one("h4 a")
                or item.select_one("h3 a")
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
        """Verifica si hay página siguiente en paginación Jumpseller."""
        # Buscar link rel=next o número de página siguiente en la paginación
        next_link = soup.select_one("a[rel='next'], .pagination a.next, li.next a")
        if next_link:
            return True

        # Alternativa: si existe el link de la siguiente página en .pagination
        pagination = soup.select(".pagination a, .paging a, nav.pagination a")
        next_page_str = str(current_page + 1)
        for link in pagination:
            if link.get_text(strip=True) == next_page_str:
                return True
            href = link.get("href", "")
            if f"page={current_page + 1}" in href:
                return True

        return False
