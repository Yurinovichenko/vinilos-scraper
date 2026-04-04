"""
shopify.py — Scraper para tiendas Shopify.

Usa el endpoint nativo /collections/{handle}/products.json?limit=250&page=N.
Sin HTML parsing — respuesta JSON directa. La más confiable del conjunto.

Tiendas cubiertas:
  Hitway, Needle, AltoQueRecordsNuevos, AltoQueRecordsOriginales,
  VinilosPorMayor, CycoRecords
"""

import asyncio
import logging
from typing import Optional

import httpx

from scrapers.base import BaseScraper, Product, ScrapeError
from scrapers.stores import StoreConfig

logger = logging.getLogger(__name__)


class ShopifyScraper(BaseScraper):
    """Scraper para tiendas Shopify via /products.json API."""

    def __init__(self, store: StoreConfig):
        super().__init__(store)
        self.limit: int = 0  # 0 = sin límite (para tests: se puede setear externamente)

    def _products_json_url(self, page: int) -> str:
        collection = self.store.shopify_collection or "all"
        return f"{self.store.base_url}/collections/{collection}/products.json"

    async def scrape(self) -> tuple[list[Product], Optional[ScrapeError]]:
        products: list[Product] = []
        page = 1

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30.0,
        ) as client:
            while True:
                url = self._products_json_url(page)
                params = {"limit": 250, "page": page}

                # Nota: /products.json no soporta filtro por product_type como parámetro.
                # El filtro se aplica en _parse_product (post-scraping).

                try:
                    resp = await self.fetch_with_timeout_retry(
                        client, url, params=params,
                        headers_override={
                            "Accept": "application/json",
                            "Accept-Encoding": "identity",
                        },
                    )
                except ScrapeError as e:
                    if products:
                        # Tenemos datos parciales — reportar error pero continuar
                        logger.warning(
                            f"[{self.store.name}] Error en página {page} con {len(products)} "
                            f"productos ya obtenidos: {e.error_type}"
                        )
                        return products, e
                    return [], e

                try:
                    data = resp.json()
                except Exception as exc:
                    logger.error(f"[{self.store.name}] JSON inválido en página {page}: {exc}")
                    break

                items = data.get("products", [])
                if not items:
                    break  # Sin más productos

                for item in items:
                    # Pre-filtro por tipo: no cuenta como fallo de parseo
                    if self.store.shopify_product_type:
                        pt = item.get("product_type", "")
                        if pt.lower() != self.store.shopify_product_type.lower():
                            continue

                    product = self._parse_product(item)
                    if product:
                        products.append(product)
                        self.record_parse_attempt(True)
                    else:
                        self.record_parse_attempt(False)

                logger.info(f"[{self.store.name}] Página {page}: {len(items)} productos ({len(products)} total)")

                # Límite para tests
                if self.limit and len(products) >= self.limit:
                    break

                # Shopify no tiene campo "has_next_page" en este endpoint.
                # Si la página retorna menos de 250 productos, es la última.
                if len(items) < 250:
                    break

                page += 1
                await self._delay()

        logger.info(f"[{self.store.name}] Total: {len(products)} productos en {page} páginas")
        return products, None

    # Palabras que indican que el vendor es tienda/discográfica, no artista
    _STORE_INDICATOR_WORDS = {"tienda", "online", "books", "libros", "store", "shop", "vinilos"}
    # Sufijos de discográfica (en este contexto, el artista está en el título)
    _LABEL_SUFFIXES = ("records", "records.", "music", "musica", "discos", "label", "entertainment")

    def _vendor_is_store(self, vendor: str) -> bool:
        """
        Heurística para detectar si el vendor es el nombre de la tienda
        o una discográfica (en cuyo caso, el artista está en el título).
        """
        if not vendor:
            return True
        v = vendor.lower().strip()
        # Vendedor con tagline: "Nombre - Descripción" → es tienda
        if " - " in v:
            return True
        # Contiene palabras típicas de tienda
        words = set(v.split())
        if words & self._STORE_INDICATOR_WORDS:
            return True
        # Más de 5 palabras → probablemente descripción de tienda
        if len(v.split()) > 5:
            return True
        # Termina en sufijo de discográfica → es label, artista en título
        if any(v.endswith(suffix) for suffix in self._LABEL_SUFFIXES):
            return True
        return False

    def _parse_product(self, item: dict) -> Optional[Product]:
        """Extrae un Product desde un item de /products.json de Shopify."""
        import re as _re
        try:
            title_raw = item.get("title", "").strip()
            vendor = item.get("vendor", "").strip()

            # Limpiar título: quitar "| VINILO", "| LP", suffixes al final
            title = _re.sub(r"\s*\|\s*(?:vinilo|lp|vinyl)\s*$", "", title_raw, flags=_re.IGNORECASE).strip()
            # Quitar prefijos como "(PREVENTA)", "(PRE-ORDEN)"
            title = _re.sub(r"^\((?:PREVENTA|PRE[-\s]?ORDEN|PRE[-\s]?ORDER)\)\s*", "", title, flags=_re.IGNORECASE).strip()

            product_type = item.get("product_type", "")

            # Decidir si el vendor es el artista real o no
            # Excluir cuando vendor == product_type (ej: "Vinilo" en CycoRecords)
            vendor_same_as_type = vendor.lower() == product_type.lower() if product_type else False
            if vendor and not self._vendor_is_store(vendor) and not vendor_same_as_type:
                # Vendor es el artista; title es el álbum
                artist = vendor
                album = title
            else:
                # Parsear "ARTISTA - ÁLBUM" o "ARTISTA – ÁLBUM" (en-dash) desde el título
                # Normalizar en-dash/em-dash a guion y limpiar sufijos de formato
                title_norm = title.replace("–", " - ").replace("—", " - ")
                title_norm = _re.sub(r"\s*-\s*Vinilo\s+(?:Simple|Doble|Triple)\s*$", "", title_norm, flags=_re.IGNORECASE).strip()
                title_norm = _re.sub(r"\s*-\s*Vinilo\s*$", "", title_norm, flags=_re.IGNORECASE).strip()
                if " - " in title_norm:
                    parts = title_norm.split(" - ", 1)
                    artist, album = parts[0].strip(), parts[1].strip()
                elif ": " in title_norm:
                    parts = title_norm.split(": ", 1)
                    artist, album = parts[0].strip(), parts[1].strip()
                else:
                    artist = ""
                    album = title_norm

            # Precio desde la primera variante
            variants = item.get("variants", [])
            price = 0
            available = False
            if variants:
                first = variants[0]
                price_str = first.get("price", "0")
                price = self.parse_price(str(price_str))
                # available si al menos una variante tiene inventario
                available = any(
                    v.get("available", False) or v.get("inventory_quantity", 0) > 0
                    for v in variants
                )

            # URL del producto
            handle = item.get("handle", "")
            url = f"{self.store.base_url}/products/{handle}" if handle else ""

            # Filtrar productos sin datos esenciales
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
            logger.debug(f"[{self.store.name}] Error parseando producto {item.get('id', '?')}: {exc}")
            return None
