"""
diagnose.py — Auto-diagnóstico y retry diferenciado de tiendas fallidas.

Clasifica errores y aplica correcciones automáticas antes de reintentar:
  DOWN          → No reintentar (tienda offline)
  BLOCKED       → No reintentar (requiere revisión manual)
  RATE_LIMITED  → Esperar más y reintentar con delay mayor
  TIMEOUT       → Reintentar con timeout mayor
  STRUCTURE_CHANGED → No reintentar (requiere revisión manual)
  DATA_OUTLIER  → Reintentar (caso edge)
  UNKNOWN       → Reintentar 1 vez
"""

import asyncio
import logging

from scrapers.base import Product, ScrapeError
from scrapers.stores import STORE_BY_NAME

logger = logging.getLogger("diagnose")

# Errores que NO tienen sentido reintentar automáticamente
NON_RECOVERABLE = {"DOWN", "BLOCKED", "STRUCTURE_CHANGED"}


async def diagnose_and_retry(
    errors: list[ScrapeError],
) -> tuple[list[Product], list[ScrapeError]]:
    """
    Analiza errores, aplica correcciones y reintenta donde tiene sentido.

    Retorna:
      fixed     — productos recuperados en el retry
      remaining — errores que no se pudieron resolver
    """
    from main import get_scraper, run_store  # Import tardío para evitar ciclo

    recovered: list[Product] = []
    remaining: list[ScrapeError] = []

    for error in errors:
        store = STORE_BY_NAME.get(error.store_name)
        if not store or not store.enabled:
            remaining.append(error)
            continue

        if error.error_type in NON_RECOVERABLE:
            logger.warning(
                f"[{error.store_name}] {error.error_type} — no reintentable. "
                f"Requiere revisión manual."
            )
            remaining.append(error)
            continue

        if error.error_type == "RATE_LIMITED":
            logger.info(f"[{error.store_name}] RATE_LIMITED — esperando 120s antes de reintentar")
            await asyncio.sleep(120)
            # Aumentar delay para esta tienda temporalmente
            original_delay = store.delay_min, store.delay_max
            store.delay_min = store.delay_min * 3
            store.delay_max = store.delay_max * 3
            products, new_errors = await run_store(store)
            store.delay_min, store.delay_max = original_delay
            if not new_errors:
                logger.info(f"[{error.store_name}] Recuperado tras RATE_LIMITED retry")
                recovered.extend(products)
            else:
                logger.error(f"[{error.store_name}] Fallo definitivo tras retry: {new_errors[0].error_type}")
                remaining.extend(new_errors)

        elif error.error_type == "TIMEOUT":
            logger.info(f"[{error.store_name}] TIMEOUT — reintentando con timeout 60s")
            products, new_errors = await run_store(store)
            if not new_errors:
                logger.info(f"[{error.store_name}] Recuperado tras TIMEOUT retry")
                recovered.extend(products)
            else:
                remaining.extend(new_errors)

        else:
            # UNKNOWN, DATA_OUTLIER: 1 reintento genérico
            logger.info(f"[{error.store_name}] {error.error_type} — reintentando")
            await asyncio.sleep(10)
            products, new_errors = await run_store(store)
            if not new_errors:
                logger.info(f"[{error.store_name}] Recuperado")
                recovered.extend(products)
            else:
                remaining.extend(new_errors)

    if remaining:
        logger.warning(f"Tiendas irresolubles tras diagnóstico: {[e.store_name for e in remaining]}")

    return recovered, remaining
