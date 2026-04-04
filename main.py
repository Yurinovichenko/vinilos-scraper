"""
main.py — Orquestador del scraper de vinilos chilenos.

Flujo:
  1. Cargar configuración de 35 tiendas
  2. Ejecutar scrapers en paralelo (async, semáforo por dominio)
  3. Diagnosticar y reintentar tiendas fallidas
  4. Normalizar nombres (Capas 1 + 2 locales)
  5. Sanity check vs run anterior
  6. Generar Excel + JSON para la web
  7. Persistir estado (last_run_stats.json, store_status.json)

Uso:
  python main.py               # Scraping completo
  python main.py --dry-run     # Solo lista tiendas, no scrapea
  python main.py --stores musicland,billboard  # Solo tiendas indicadas
  python main.py --limit 50    # Máx 50 productos por tienda (para tests)
"""

import argparse
import asyncio
import importlib
import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from scrapers.base import Product, ScrapeError
from scrapers.normalizer import detect_irregularities
from scrapers.stores import ACTIVE_STORES, STORE_BY_NAME, StoreConfig

# ─── Rutas ────────────────────────────────────────────────────
ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

LAST_RUN_STATS = DATA_DIR / "last_run_stats.json"
STORE_STATUS = DATA_DIR / "store_status.json"

# ─── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(DATA_DIR / "scraper.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")


# ─── Carga dinámica de scrapers ───────────────────────────────

PLATFORM_MODULES = {
    "woocommerce": "scrapers.woocommerce",
    "shopify": "scrapers.shopify",
    "prestashop": "scrapers.prestashop",
    "jumpseller": "scrapers.jumpseller",
    "bsale": "scrapers.bsale",
    "nuvemshop": "scrapers.nuvemshop",
    "odoo": "scrapers.odoo",
}


def get_scraper(store: StoreConfig):
    """Instancia el scraper correcto según la plataforma."""
    module_path = PLATFORM_MODULES.get(store.platform)
    if not module_path:
        raise ValueError(f"Plataforma desconocida: {store.platform}")
    module = importlib.import_module(module_path)
    cls = getattr(module, f"{store.platform.capitalize()}Scraper", None)
    if cls is None:
        # Buscar clase que termina en "Scraper"
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and name.endswith("Scraper") and name != "BaseScraper":
                cls = obj
                break
    if cls is None:
        raise ImportError(f"No se encontró clase Scraper en {module_path}")
    return cls(store)


# ─── Ejecución de scrapers ────────────────────────────────────

async def run_store(store: StoreConfig, limit: int = 0) -> tuple[list[Product], list[ScrapeError]]:
    """Ejecuta el scraper de una tienda y retorna (productos, errores)."""
    try:
        scraper = get_scraper(store)
        if limit:
            scraper.limit = limit  # Para tests
        products, error = await scraper.scrape()
        if error:
            return products, [error]

        # Verificar cambio de estructura
        issue = scraper.check_structure_change()
        if issue == "STRUCTURE_CHANGED":
            logger.error(f"[{store.name}] STRUCTURE_CHANGED — failure_rate={scraper.failure_rate:.1%}")
            return products, [ScrapeError(
                store.name, "STRUCTURE_CHANGED",
                f"failure_rate={scraper.failure_rate:.1%} — estructura HTML posiblemente cambió",
                failure_rate=scraper.failure_rate,
                recoverable=False,
            )]

        return products, []

    except ScrapeError as e:
        e.timestamp = datetime.now(timezone.utc).isoformat()
        logger.error(f"[{store.name}] {e.error_type}: {e.message}")
        return [], [e]
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"[{store.name}] Error inesperado: {e}\n{tb}")
        return [], [ScrapeError(
            store.name, "UNKNOWN", str(e), traceback=tb,
            timestamp=datetime.now(timezone.utc).isoformat(),
            recoverable=True,
        )]


async def run_all_scrapers(
    stores: list[StoreConfig],
    limit: int = 0,
) -> tuple[list[Product], list[ScrapeError]]:
    """Ejecuta todos los scrapers en paralelo."""
    tasks = [run_store(store, limit) for store in stores]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    all_products: list[Product] = []
    all_errors: list[ScrapeError] = []
    for products, errors in results:
        all_products.extend(products)
        all_errors.extend(errors)

    return all_products, all_errors


# ─── Sanity check ─────────────────────────────────────────────

def sanity_check(
    results_by_store: dict[str, int],
    last_stats: dict,
    threshold: float = 0.50,
) -> list[str]:
    """
    Compara conteos actuales vs run anterior.
    Alerta si alguna tienda tiene >50% de reducción.
    Retorna lista de alertas.
    """
    alerts = []
    prev = last_stats.get("stores", {})
    for store_name, count in results_by_store.items():
        prev_count = prev.get(store_name, {}).get("count", 0)
        if prev_count > 10 and count < prev_count * (1 - threshold):
            alerts.append(
                f"⚠️ {store_name}: {count} registros (antes: {prev_count}, "
                f"reducción: {(1 - count/prev_count):.0%})"
            )
    return alerts


# ─── Persistencia de estado ───────────────────────────────────

def load_json(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def update_store_status(
    store_status: dict,
    errors: list[ScrapeError],
    successful_stores: set[str],
    run_date: str,
) -> dict:
    """Actualiza el historial acumulativo de fallos por tienda."""
    for err in errors:
        entry = store_status.get(err.store_name, {
            "consecutive_failures": 0,
            "last_success": None,
            "failure_type": None,
        })
        entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1
        entry["failure_type"] = err.error_type
        entry["last_failure"] = run_date
        store_status[err.store_name] = entry

    for store_name in successful_stores:
        store_status[store_name] = {
            "consecutive_failures": 0,
            "last_success": run_date,
            "failure_type": None,
        }

    return store_status


# ─── Main ──────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Scraper de vinilos chilenos")
    p.add_argument("--dry-run", action="store_true", help="Lista tiendas sin scraper")
    p.add_argument("--stores", help="Tiendas específicas separadas por coma")
    p.add_argument("--limit", type=int, default=0, help="Máx productos por tienda (0=todos)")
    return p.parse_args()


async def main():
    args = parse_args()
    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    logger.info(f"=== Vinilos Scraper v2.0 — {run_date} ===")

    # Seleccionar tiendas
    if args.stores:
        names = [s.strip() for s in args.stores.split(",")]
        stores = [STORE_BY_NAME[n] for n in names if n in STORE_BY_NAME]
        missing = [n for n in names if n not in STORE_BY_NAME]
        if missing:
            logger.warning(f"Tiendas no encontradas: {missing}")
    else:
        stores = ACTIVE_STORES

    if args.dry_run:
        logger.info(f"DRY RUN — {len(stores)} tiendas activas:")
        for s in stores:
            logger.info(f"  [{s.platform:12s}] {s.name} → {s.vinyl_url}")
        return

    logger.info(f"Iniciando scraping de {len(stores)} tiendas...")

    # 1. Cargar estado anterior
    last_stats = load_json(LAST_RUN_STATS)
    store_status = load_json(STORE_STATUS)

    # 2. Ejecutar scrapers
    products, errors = await run_all_scrapers(stores, limit=args.limit)
    logger.info(f"Scraping completado: {len(products)} productos, {len(errors)} errores")

    # 3. Diagnóstico y retry (importado aquí para evitar ciclo)
    from diagnose import diagnose_and_retry
    if errors:
        fixed, remaining = await diagnose_and_retry(errors)
        products.extend(fixed)
        logger.info(f"Diagnóstico: {len(fixed)} recuperados, {len(remaining)} irresolubles")
    else:
        remaining = []

    # 4. Normalización Capas 1+2 (local, gratis)
    logger.info("Normalizando nombres (Capas 1+2)...")
    products = detect_irregularities(products)

    # 5. Sanity check
    results_by_store = {}
    for p in products:
        results_by_store[p.store] = results_by_store.get(p.store, 0) + 1

    sanity_alerts = sanity_check(results_by_store, last_stats)
    if sanity_alerts:
        logger.warning("ALERTAS DE SANITY CHECK:")
        for alert in sanity_alerts:
            logger.warning(f"  {alert}")

    # 6. Generar Excel y JSON
    from generate_web import generate_excel, generate_json
    xlsx_path = generate_excel(products, remaining, sanity_alerts, results_by_store, last_stats, run_date)
    json_path = generate_json(products)
    logger.info(f"Excel: {xlsx_path}")
    logger.info(f"JSON: {json_path}")

    # 7. Persistir estado
    successful = set(results_by_store.keys())
    store_status = update_store_status(store_status, remaining, successful, run_date)
    # Recopilar productos marcados para revisión MusicBrainz (Capa 3)
    needs_review = [
        {"artist": p.artist_norm, "album": p.album_norm}
        for p in products
        if p.mb_id == "NEEDS_REVIEW"
    ]
    # Deduplicar
    seen_nr = set()
    unique_review = []
    for item in needs_review:
        k = f"{item['artist']}|||{item['album']}"
        if k not in seen_nr:
            seen_nr.add(k)
            unique_review.append(item)

    new_stats = {
        "run_date": run_date,
        "total_products": len(products),
        "stores": {name: {"count": count, "status": "ok"} for name, count in results_by_store.items()},
        "needs_review": unique_review,
    }
    save_json(LAST_RUN_STATS, new_stats)
    save_json(STORE_STATUS, store_status)
    logger.info("Estado persistido en data/")

    # 8. Enviar email
    from send_email import send_report
    send_report(
        xlsx_path=xlsx_path,
        products=products,
        errors=remaining,
        sanity_alerts=sanity_alerts,
        run_date=run_date,
        results_by_store=results_by_store,
        last_stats=last_stats,
    )

    logger.info(f"=== Completado: {len(products)} productos de {len(results_by_store)} tiendas ===")


if __name__ == "__main__":
    asyncio.run(main())
