"""
nuvemshop.py — Scraper para tiendas Nuvemshop/TiendaNube.

Los productos tienen sus datos completos en el atributo data-variants (JSON).
Incluye precio, disponibilidad, SKU, etc. Sin necesidad de scraping adicional.

Tiendas cubiertas:
  OrejaMusic
"""

import json
import logging
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, Product, ScrapeError
from scrapers.stores import StoreConfig

logger = logging.getLogger(__name__)

# Palabras que indican que el producto NO es vinilo (para filtrar en OrejaMusic)
_NON_VINYL_PREFIXES = ("CD-", "CD –", "CD–", "DVD-", "DVD –", "BLU-RAY")


class NuvemshopScraper(BaseScraper):
    """Scraper para tiendas Nuvemshop (TiendaNube)."""

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
        seen_ids: set[str] = set()

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
                items = soup.select(".product-item[data-product-id]")
                if not items:
                    if page == 1:
                        logger.warning(f"[{self.store.name}] Página 1 sin productos")
                        self.record_parse_attempt(False)
                    break

                # Detectar paginación duplicada (Nuvemshop a veces repite la última página)
                page_ids = {i.get("data-product-id", "") for i in items}
                if page_ids & seen_ids and page > 1:
                    break  # Página repetida = fin
                seen_ids.update(page_ids)

                for item in items:
                    product = self._parse_product(item)
                    if product:
                        products.append(product)
                        self.record_parse_attempt(True)
                    else:
                        self.record_parse_attempt(False)

                logger.info(f"[{self.store.name}] Página {page}: {len(items)} items ({len(products)} vinilos total)")

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
            title_el = item.select_one("[class*=name], .product-item__name, h3, h2")
            if not title_el:
                return None
            raw_title = title_el.get_text(strip=True)
            if not raw_title:
                return None

            # Filtrar productos que no son vinilos (OrejaMusic tiene CDs, DVDs)
            for prefix in _NON_VINYL_PREFIXES:
                if raw_title.upper().startswith(prefix.upper()):
                    return None

            # Quitar prefijo "VINILO-" o "VINILO " del título
            import re as _re
            raw_title = _re.sub(r'^VINILO[-\s]+', '', raw_title, flags=_re.IGNORECASE).strip()

            title_norm = raw_title.replace("–", " - ").replace("—", " - ")
            if " - " in title_norm:
                parts = title_norm.split(" - ", 1)
                artist = parts[0].strip()
                album = parts[1].strip()
            else:
                artist = ""
                album = raw_title

            # ── Precio y disponibilidad desde data-variants ──────────────
            price = 0
            available = False
            variants_raw = item.get("data-variants", "[]")
            try:
                variants = json.loads(variants_raw)
                for v in variants:
                    if v.get("available") or v.get("stock", 0) > 0:
                        available = True
                    p = v.get("price_number", 0)
                    if p and p > price:
                        price = p
                # Si no hay variante disponible pero tiene precio, puede estar agotado
                if not available and variants:
                    price = variants[0].get("price_number", 0)
            except (json.JSONDecodeError, TypeError):
                pass

            if price == 0:
                return None

            # ── URL ──────────────────────────────────────────────────────
            link_el = item.select_one("a[href*='/productos/'], a[href]")
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
        return bool(
            soup.select_one(
                "a[rel='next'], .pagination a.next, "
                "[class*=pagination] a[aria-label*=iguiente], "
                "li.next a"
            )
        )
