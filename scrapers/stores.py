"""
stores.py — Configuración centralizada de las 35 tiendas de vinilos chilenas.

Plataformas detectadas:
  - WooCommerce  (~15 tiendas): paginación /page/N/, HTML parsing
  - Shopify       (6 tiendas): /products.json API, sin HTML
  - PrestaShop    (4 tiendas): var prestashop JSON + HTML pagination
  - Jumpseller    (3 tiendas): ?page=N HTML, server-side (sin Playwright)
  - Bsale         (2 tiendas): window.INIT JSON embebido
  - Nuvemshop     (1 tienda):  OrejaMusic
  - Odoo          (1 tienda):  LaTiendaNacional
  - Wix           (1 tienda):  ChincolaRecords — DESHABILITADA (inescrapeable)
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StoreConfig:
    name: str               # Nombre legible para reportes
    platform: str           # woocommerce | shopify | prestashop | jumpseller | bsale | nuvemshop | odoo
    base_url: str           # URL raíz de la tienda
    vinyl_url: str          # URL de la categoría/colección de vinilos
    concurrency: int = 2    # Máx requests simultáneas al dominio
    delay_min: float = 1.0  # Delay mínimo entre requests (segundos)
    delay_max: float = 3.0  # Delay máximo entre requests (segundos)
    enabled: bool = True    # False = excluida del scraping automático
    notes: str = ""         # Comentarios para diagnóstico

    # Shopify: handle de la colección
    shopify_collection: Optional[str] = None
    # Shopify: filtrar por product_type (ej: "Vinilo" en CycoRecords)
    shopify_product_type: Optional[str] = None

    # WooCommerce/Jumpseller: parámetro de paginación
    page_param: Optional[str] = None  # Ej: "page" para ?page=N


STORES: list[StoreConfig] = [

    # ─────────────────────────────────────────────────────────
    # WOOCOMMERCE (~15 tiendas)
    # Paginación: /categoria/page/N/ o ?page=N
    # Parser: BeautifulSoup4, selector li.product o .product-item
    # ─────────────────────────────────────────────────────────
    StoreConfig(
        name="Musicland",
        platform="woocommerce",
        base_url="https://musicland.cl",
        vinyl_url="https://musicland.cl/categoria-producto/vinilos/",
        concurrency=1,
        delay_min=3.0,
        delay_max=6.0,
        notes="Flatsome theme + Wordfence; anti-bot activo",
    ),
    StoreConfig(
        name="DisqueriaKYD",
        platform="woocommerce",
        base_url="https://disqueriakyd.cl",
        vinyl_url="https://disqueriakyd.cl/tienda/",
        concurrency=1,
        delay_min=3.0,
        delay_max=5.0,
        notes="Salient theme + Wordfence; usa /tienda/ no /product-category/",
    ),
    StoreConfig(
        name="Sonar",
        platform="woocommerce",
        base_url="https://www.sonartienda.cl",
        vinyl_url="https://www.sonartienda.cl/categorias/vinilos/",
        concurrency=2,
        delay_min=1.5,
        delay_max=3.0,
        notes="Usa /categorias/ en lugar de /categoria-producto/",
    ),
    StoreConfig(
        name="BaltazarVinyl",
        platform="woocommerce",
        base_url="https://baltazarvinyl.cl",
        vinyl_url="https://baltazarvinyl.cl/categoria-producto/vinilos/",
        concurrency=2,
        delay_min=2.0,
        delay_max=4.0,
        notes="Categoría específica de vinilos (Astra theme); servidor lento",
    ),
    StoreConfig(
        name="RockStore",
        platform="woocommerce",
        base_url="https://rockstorevinilos.cl",
        vinyl_url="https://rockstorevinilos.cl/",
        concurrency=2,
        delay_min=1.5,
        delay_max=3.0,
        notes="Homepage es el catálogo completo (tienda 100% vinilos, sin categoría específica)",
    ),
    StoreConfig(
        name="PuntoMusical",
        platform="woocommerce",
        base_url="https://puntomusical.cl",
        vinyl_url="https://puntomusical.cl/categoria-producto/vinilos/",
        concurrency=2,
        delay_min=1.5,
        delay_max=3.0,
        notes="JetWoo Builder + Elementor (div.jet-woo-products__item, h5.jet-woo-product-title)",
    ),
    StoreConfig(
        name="MusicJungle",
        platform="woocommerce",
        base_url="https://musicjungle.cl",
        vinyl_url="https://musicjungle.cl/categoria-producto/discos/vinilo/",
        concurrency=2,
        delay_min=1.5,
        delay_max=3.0,
        notes="URL real: /categoria-producto/discos/vinilo/ (subcategoría de discos)",
    ),
    StoreConfig(
        name="ObiVinilos",
        platform="woocommerce",
        base_url="https://www.obivinilos.cl",
        vinyl_url="https://www.obivinilos.cl/product-category/vinilos/",
        concurrency=1,
        delay_min=3.0,
        delay_max=6.0,
        notes="Sitio retorna HTML mínimo (~833 bytes) — posible JS-rendering o servidor down. Monitorear.",
    ),
    StoreConfig(
        name="Vinitrola",
        platform="woocommerce",
        base_url="https://www.vinitrola.cl",
        vinyl_url="https://www.vinitrola.cl/tienda/",
        concurrency=2,
        delay_min=1.5,
        delay_max=3.0,
        notes="Usa /tienda/ como catálogo general de vinilos",
    ),
    StoreConfig(
        name="BlackVinyl",
        platform="woocommerce",
        base_url="https://blackvinyl.cl",
        vinyl_url="https://blackvinyl.cl/product-category/vinilos/",
        concurrency=2,
        delay_min=1.5,
        delay_max=3.0,
    ),
    StoreConfig(
        name="VinilosAlvaro",
        platform="woocommerce",
        base_url="https://vinilosalvaro.cl",
        vinyl_url="https://vinilosalvaro.cl/tienda/",
        concurrency=2,
        delay_min=1.5,
        delay_max=3.0,
        notes="Usa /tienda/ como catálogo",
    ),
    StoreConfig(
        name="Kolala",
        platform="woocommerce",
        base_url="https://kolala.cl",
        vinyl_url="https://kolala.cl/product-category/vinilos/",
        concurrency=2,
        delay_min=1.5,
        delay_max=3.0,
    ),
    StoreConfig(
        name="FGHighEnd",
        platform="woocommerce",
        base_url="https://fghighend.cl",
        vinyl_url="https://fghighend.cl/categoria-producto/vinilos/",
        concurrency=2,
        delay_min=1.5,
        delay_max=3.0,
    ),
    StoreConfig(
        name="HighEnd",
        platform="woocommerce",
        base_url="https://highend.cl",
        vinyl_url="https://highend.cl/sacd-vinilos/",
        concurrency=2,
        delay_min=1.5,
        delay_max=3.0,
        notes="Categoría combinada SACD + Vinilos",
    ),
    StoreConfig(
        name="VinilotecaNuevos",
        platform="woocommerce",
        base_url="https://viniloteca.cl",
        vinyl_url="https://viniloteca.cl/",
        concurrency=2,
        delay_min=1.5,
        delay_max=3.0,
        notes="Homepage muestra todos los vinilos (28 aprox). Sin paginación aparente.",
    ),
    StoreConfig(
        name="VinilotecaUsados",
        platform="woocommerce",
        base_url="https://viniloteca.cl",
        vinyl_url="https://viniloteca.cl/",
        concurrency=2,
        delay_min=1.5,
        delay_max=3.0,
        notes="Misma URL que Nuevos — se consolidan. TODO: buscar URL de usados.",
        enabled=False,  # Desactivar temporalmente hasta confirmar URL de usados
    ),

    # ─────────────────────────────────────────────────────────
    # SHOPIFY (6 tiendas)
    # Endpoint: /collections/{handle}/products.json?limit=250&page=N
    # Sin HTML parsing, respuesta JSON nativa
    # ─────────────────────────────────────────────────────────
    StoreConfig(
        name="Hitway",
        platform="shopify",
        base_url="https://hitway.cl",
        vinyl_url="https://hitway.cl/collections/vinilos",
        shopify_collection="vinilos",
        concurrency=4,
        delay_min=0.5,
        delay_max=1.5,
    ),
    StoreConfig(
        name="Needle",
        platform="shopify",
        base_url="https://needle.cl",
        vinyl_url="https://needle.cl/collections/vinilos",
        shopify_collection="vinilos",
        concurrency=4,
        delay_min=0.5,
        delay_max=1.5,
    ),
    StoreConfig(
        name="AltoQueRecordsNuevos",
        platform="shopify",
        base_url="https://altoquerecords.cl",
        vinyl_url="https://altoquerecords.cl/collections/vinilos-nuevos",
        shopify_collection="vinilos-nuevos",
        concurrency=4,
        delay_min=0.5,
        delay_max=1.5,
    ),
    StoreConfig(
        name="AltoQueRecordsOriginales",
        platform="shopify",
        base_url="https://altoquerecords.cl",
        vinyl_url="https://altoquerecords.cl/collections/all",
        shopify_collection="all",
        concurrency=4,
        delay_min=0.5,
        delay_max=1.5,
        notes="Colección 'all' — misma tienda que Nuevos; cubre vinilos originales/usados",
    ),
    StoreConfig(
        name="VinilosPorMayor",
        platform="shopify",
        base_url="https://vinilospormayor.cl",
        vinyl_url="https://vinilospormayor.cl/collections/all",
        shopify_collection="all",
        concurrency=4,
        delay_min=0.5,
        delay_max=1.5,
    ),
    StoreConfig(
        name="CycoRecords",
        platform="shopify",
        base_url="https://cycorecords.cl",
        vinyl_url="https://cycorecords.cl/collections/all",
        shopify_collection="all",
        shopify_product_type="Vinilo",  # Filtrar solo vinilos (no poleras, CDs)
        concurrency=4,
        delay_min=0.5,
        delay_max=1.5,
        notes="Colección 'all' incluye poleras y CDs; filtrar por product_type='Vinilo'",
    ),

    # ─────────────────────────────────────────────────────────
    # PRESTASHOP (4 tiendas)
    # HTML parsing + extracción de var prestashop JSON
    # ─────────────────────────────────────────────────────────
    StoreConfig(
        name="ElevenStore",
        platform="prestashop",
        base_url="https://elevenstore.cl",
        vinyl_url="https://elevenstore.cl/elevenstore/22-vinilos",
        concurrency=2,
        delay_min=1.5,
        delay_max=3.0,
        notes="PS 1.6; categoría numérica /22-vinilos; paginación ?p=N",
    ),
    StoreConfig(
        name="MusicWorld",
        platform="prestashop",
        base_url="https://www.musicworld.cl",
        vinyl_url="https://www.musicworld.cl/214-vinilo",
        concurrency=2,
        delay_min=1.5,
        delay_max=3.0,
        notes="PS 1.7 custom; categoría /214-vinilo; paginación ?page=N; ~977 productos",
    ),
    StoreConfig(
        name="MusicLife",
        platform="prestashop",
        base_url="https://musiclife.cl",
        vinyl_url="https://musiclife.cl/categoria/vinilos",
        concurrency=2,
        delay_min=1.5,
        delay_max=3.0,
        notes="PS 1.7 custom; 45 productos en página única (sin paginación); precios cargados via JS (price=0)",
    ),
    StoreConfig(
        name="Disqueria12Pulgadas",
        platform="prestashop",
        base_url="https://disqueria12pulgadas.cl",
        vinyl_url="https://disqueria12pulgadas.cl/",
        concurrency=2,
        delay_min=1.5,
        delay_max=3.0,
        notes="PS 1.6; homepage muestra todos los vinilos (~32); 88% fuera de stock es normal para usados",
    ),

    # ─────────────────────────────────────────────────────────
    # JUMPSELLER (3 tiendas)
    # Paginación server-side: ?page=N — sin Playwright
    # ─────────────────────────────────────────────────────────
    StoreConfig(
        name="SVinilos",
        platform="jumpseller",
        base_url="https://www.svinilos.cl",
        vinyl_url="https://www.svinilos.cl/",
        page_param="page",
        concurrency=2,
        delay_min=1.5,
        delay_max=3.0,
        notes="Homepage es el catálogo completo (tienda 100% vinilos); /vinilos retorna 404",
    ),
    StoreConfig(
        name="FuturoPrimitivo",
        platform="jumpseller",
        base_url="https://futuroprimitivo.cl",
        vinyl_url="https://futuroprimitivo.cl/vinilos",
        page_param="page",
        concurrency=2,
        delay_min=1.5,
        delay_max=3.0,
    ),
    StoreConfig(
        name="DisqueriaBazarDeCulto",
        platform="jumpseller",
        base_url="https://www.disqueriabazardeculto.cl",
        vinyl_url="https://www.disqueriabazardeculto.cl/vinilos",
        page_param="page",
        concurrency=2,
        delay_min=1.5,
        delay_max=3.0,
    ),

    # ─────────────────────────────────────────────────────────
    # BSALE (2 tiendas)
    # Extracción de window.INIT JSON embebido en HTML
    # ─────────────────────────────────────────────────────────
    StoreConfig(
        name="Billboard",
        platform="bsale",
        base_url="https://www.billboard.cl",
        vinyl_url="https://www.billboard.cl/collection/vinilos",
        concurrency=2,
        delay_min=1.5,
        delay_max=3.0,
        notes="Bsale; colección /collection/vinilos; window.INIT JSON embebido",
    ),
    StoreConfig(
        name="PlazaMusica",
        platform="bsale",
        base_url="https://www.plazamusica.cl",
        vinyl_url="https://www.plazamusica.cl/collection/vinilos",
        concurrency=2,
        delay_min=1.5,
        delay_max=3.0,
        notes="Bsale; colección /collection/vinilos; window.INIT JSON embebido",
    ),

    # ─────────────────────────────────────────────────────────
    # NUVEMSHOP / TIENDANUBE (1 tienda)
    # ─────────────────────────────────────────────────────────
    StoreConfig(
        name="OrejaMusic",
        platform="nuvemshop",
        base_url="https://orejamusic.com",
        vinyl_url="https://orejamusic.com/vinilos",
        concurrency=2,
        delay_min=1.5,
        delay_max=3.0,
    ),

    # ─────────────────────────────────────────────────────────
    # ODOO (1 tienda)
    # ─────────────────────────────────────────────────────────
    StoreConfig(
        name="LaTiendaNacional",
        platform="odoo",
        base_url="https://www.latiendanacional.cl",
        vinyl_url="https://www.latiendanacional.cl/shop/category/musica-formato-vinilo-191",
        concurrency=2,
        delay_min=1.5,
        delay_max=3.0,
        notes="Odoo oe_website_sale; paginación /page/N; categoría numérica 191",
    ),

    # ─────────────────────────────────────────────────────────
    # WIX — DESHABILITADA
    # ChincolaRecords usa Wix Thunderbolt (client-side rendering)
    # Los datos estáticos de enero 2026 se incluyen directamente en el Excel.
    # ─────────────────────────────────────────────────────────
    StoreConfig(
        name="ChincolaRecords",
        platform="wix",
        base_url="https://www.chincolarecords.cl",
        vinyl_url="https://www.chincolarecords.cl/vinilos",
        enabled=False,
        notes="Wix Thunderbolt renderer — inescrapeable de forma confiable. "
              "Usar datos estáticos de enero 2026.",
    ),
]

# Índice por nombre para acceso rápido
STORE_BY_NAME: dict[str, StoreConfig] = {s.name: s for s in STORES}

# Solo tiendas habilitadas
ACTIVE_STORES: list[StoreConfig] = [s for s in STORES if s.enabled]

# Mapa de equivalencias de artistas (corrección de puntuación entre tiendas)
ARTIST_EQUIVALENCES: dict[str, str] = {
    "AC_DC": "AC/DC",
    "AC-DC": "AC/DC",
    "ACDC": "AC/DC",
    "Guns N Roses": "Guns N' Roses",
    "Guns N' Roses": "Guns N' Roses",
}
