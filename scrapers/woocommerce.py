"""
woocommerce.py — Scraper genérico para tiendas WooCommerce (~16 tiendas).

Estrategia:
  - Paginación: /categoria/page/N/ o /?page=N
  - Parser: BeautifulSoup4, selector li.product o .product-item
  - Extracción de: título (artista + álbum), precio, disponibilidad, URL
  - Semáforo por dominio para evitar bloqueos

Tiendas cubiertas:
  Musicland, DisqueriaKYD, Sonar, BaltazarVinyl, RockStore, PuntoMusical,
  MusicJungle, ObiVinilos, Vinitrola, BlackVinyl, VinilosAlvaro, Kolala,
  FGHighEnd, HighEnd, VinilotecaNuevos, VinilotecaUsados
"""

import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, Product, ScrapeError
from scrapers.stores import StoreConfig

logger = logging.getLogger(__name__)

# Indicadores de disponibilidad en WooCommerce
_OUT_OF_STOCK_CLASSES = {"outofstock", "out-of-stock", "soldout", "sold-out"}
_IN_STOCK_CLASSES = {"instock", "in-stock"}


class WoocommerceScraper(BaseScraper):
    """Scraper genérico para tiendas WooCommerce."""

    def __init__(self, store: StoreConfig):
        super().__init__(store)
        self.limit: int = 0

    def _page_url(self, page: int) -> str:
        """Construye la URL de una página de categoría WooCommerce."""
        base = self.store.vinyl_url.rstrip("/")
        if page == 1:
            return base + "/"
        return f"{base}/page/{page}/"

    async def scrape(self) -> tuple[list[Product], Optional[ScrapeError]]:
        products: list[Product] = []
        page = 1

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=45.0,
        ) as client:
            while True:
                url = self._page_url(page)
                try:
                    resp = await self.fetch_with_timeout_retry(client, url)
                except ScrapeError as e:
                    if page == 1:
                        return [], e
                    # Página no existe → fin de la paginación
                    if e.error_type in ("BLOCKED", "RATE_LIMITED"):
                        return products, e
                    break  # ConnectError o similar después de la primera página

                soup = BeautifulSoup(resp.text, "lxml")

                # Detectar fin de paginación (página 404 o sin productos)
                if resp.status_code == 404:
                    break

                items = self._find_products(soup)
                if not items:
                    # Verificar si es realmente la última página o un error de selector
                    if page == 1:
                        logger.warning(f"[{self.store.name}] Página 1 sin productos — verificar selector CSS")
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

                # Verificar si hay página siguiente
                if not self._has_next_page(soup, page):
                    break

                page += 1
                await self._delay()

        logger.info(f"[{self.store.name}] Total: {len(products)} productos en {page} páginas")
        return products, None

    def _find_products(self, soup: BeautifulSoup) -> list:
        """Encuentra los elementos de producto en la página."""
        # Probar selectores en orden de especificidad
        for selector in [
            "ul.products li.product:not(.product-category)",  # WooCommerce estándar (sin categorías)
            "ul.products li.product",
            "li.product:not(.product-category)",              # Sin padre ul.products (Sonar, Vinitrola)
            "li.product",
            "div.jet-woo-products__item",                     # PuntoMusical (JetWoo Builder + Elementor)
            "div.products div.product",
            "section.type-product",                           # BlackVinyl (tema Loobek usa section)
            "article.type-product",
            ".product-item",
        ]:
            items = soup.select(selector)
            if items:
                # Filtrar category items que se cuelan
                filtered = [i for i in items if "product-category" not in i.get("class", [])]
                if filtered:
                    return filtered
                if items:
                    return items

        return []

    def _parse_product(self, item) -> Optional[Product]:
        """Extrae un Product desde un elemento li.product de WooCommerce."""
        try:
            # ── Título (artista - álbum) ────────────────────────────────
            title_el = (
                item.select_one(".woocommerce-loop-product__title")
                or item.select_one("h2.product-title")
                or item.select_one("h5.jet-woo-product-title")  # PuntoMusical (JetWoo Builder)
                or item.select_one("h2")
                or item.select_one("h3")
                or item.select_one(".product-name")
            )
            if not title_el:
                return None
            raw_title = title_el.get_text(strip=True)

            # Intentar separar Artista - Álbum
            # Muchas tiendas usan "Artista - Álbum" o "Artista – Álbum"
            title_norm = raw_title.replace("–", " - ").replace("—", " - ")
            if " - " in title_norm:
                parts = title_norm.split(" - ", 1)
                artist = parts[0].strip()
                album = parts[1].strip()
            else:
                artist = ""
                album = raw_title

            # ── Precio ──────────────────────────────────────────────────
            price = 0
            # Precio con descuento (ins) o precio normal
            price_el = (
                item.select_one("ins .woocommerce-Price-amount")
                or item.select_one(".price .woocommerce-Price-amount")
                or item.select_one(".woocommerce-Price-amount")
                or item.select_one(".price")
            )
            if price_el:
                price = self.parse_price(price_el.get_text())

            # ── Disponibilidad ───────────────────────────────────────────
            available = True  # Por defecto disponible en WooCommerce

            # Verificar clases CSS del item
            item_classes = set(item.get("class", []))
            if item_classes & _OUT_OF_STOCK_CLASSES:
                available = False
            elif item_classes & _IN_STOCK_CLASSES:
                available = True
            else:
                # Buscar badge o texto de stock
                stock_el = item.select_one(".stock, .out-of-stock, .soldout, .add_to_cart_button")
                if stock_el:
                    stock_text = stock_el.get_text(strip=True).lower()
                    stock_cls = set(stock_el.get("class", []))
                    if stock_cls & _OUT_OF_STOCK_CLASSES or "agotado" in stock_text or "sin stock" in stock_text:
                        available = False
                    # Si tiene botón "Añadir al carrito" → disponible
                    elif "add-to-cart" in " ".join(stock_el.get("class", [])):
                        available = True

                # Si no hay botón de compra, probablemente sin stock
                buy_btn = item.select_one("a.add_to_cart_button, a.button.product_type_simple")
                if buy_btn:
                    btn_classes = set(buy_btn.get("class", []))
                    if "disabled" in btn_classes or "outofstock" in btn_classes:
                        available = False
                    else:
                        available = True

            # ── URL del producto ─────────────────────────────────────────
            link_el = item.select_one("a.woocommerce-loop-product__link") or item.select_one("a")
            url = ""
            if link_el and link_el.get("href"):
                href = link_el["href"]
                if href.startswith("http"):
                    url = href
                else:
                    url = urljoin(self.store.base_url, href)

            if not album:
                return None
            # Precio 0 en item que NO está marcado como agotado = banner/promocional, descartar
            if price == 0 and available:
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
        """Verifica si existe una página siguiente."""
        # Navegación estándar WooCommerce
        next_link = (
            soup.select_one("a.next.page-numbers")
            or soup.select_one(".woocommerce-pagination a.next")
            or soup.select_one("nav.woocommerce-pagination a[aria-label='Next']")
        )
        if next_link:
            return True

        # Buscar si la URL de la siguiente página existe en cualquier paginación
        next_url = self._page_url(current_page + 1)
        pagination = soup.select(".page-numbers a, .pagination a")
        for link in pagination:
            href = link.get("href", "")
            if f"/page/{current_page + 1}/" in href or href == next_url:
                return True

        return False
