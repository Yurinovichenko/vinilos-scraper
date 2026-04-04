"""
normalize.py — Capa 3: Normalización via MusicBrainz API (job mensual).

Lee los productos marcados como NEEDS_REVIEW en data/last_run_stats.json,
consulta MusicBrainz para obtener nombres canónicos, y actualiza
data/mb_cache.json con los resultados.

MusicBrainz API:
  - Gratuita, sin token (solo User-Agent como identificación)
  - Rate limit: 1 req/seg
  - Endpoint: https://musicbrainz.org/ws/2/release/?query=...&fmt=json

Uso:
  python normalize.py              # Procesa todos los NEEDS_REVIEW
  python normalize.py --limit 100  # Limitar queries a 100 (para test)
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

import httpx

logger = logging.getLogger("normalize")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
MB_CACHE = DATA_DIR / "mb_cache.json"

# Identificación requerida por MusicBrainz (evita bloqueo)
MB_USER_AGENT = "VinilosChileScraper/2.0 (yyurac@gmail.com)"
MB_BASE_URL = "https://musicbrainz.org/ws/2"
MB_RATE_LIMIT = 1.1  # segundos entre requests (ligeramente por encima de 1/seg)


def load_cache() -> dict:
    if MB_CACHE.exists():
        return json.loads(MB_CACHE.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    MB_CACHE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def cache_key(artist: str, album: str) -> str:
    """Clave normalizada para el cache."""
    return f"{artist.lower().strip()}|||{album.lower().strip()}"


async def query_musicbrainz(
    client: httpx.AsyncClient,
    artist: str,
    album: str,
) -> dict | None:
    """
    Consulta MusicBrainz por (artista, álbum).
    Retorna {artist_canonical, album_canonical, mb_id} o None si no encontrado.
    """
    query = f'artist:"{artist}" AND release:"{album}"'
    try:
        resp = await client.get(
            f"{MB_BASE_URL}/release/",
            params={"query": query, "fmt": "json", "limit": 5},
            headers={"User-Agent": MB_USER_AGENT},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        releases = data.get("releases", [])

        for release in releases:
            score = int(release.get("score", 0))
            if score < 85:
                continue

            mb_id = release.get("id", "")
            canonical_album = release.get("title", album)

            # Artista desde artist-credit
            credits = release.get("artist-credit", [])
            artist_parts = []
            for credit in credits:
                if isinstance(credit, dict) and "artist" in credit:
                    artist_parts.append(credit["artist"].get("name", ""))
                elif isinstance(credit, str):
                    artist_parts.append(credit)
            canonical_artist = "".join(artist_parts) or artist

            return {
                "artist_canonical": canonical_artist,
                "album_canonical": canonical_album,
                "mb_id": mb_id,
                "score": score,
            }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 503:
            logger.warning(f"MusicBrainz 503 para '{artist} - {album}'. Reintentando en 5s...")
            await asyncio.sleep(5)
        else:
            logger.warning(f"HTTP {e.response.status_code} para '{artist} - {album}'")
    except Exception as e:
        logger.debug(f"Error consultando MusicBrainz: {e}")

    return None


async def run_normalization(limit: int = 0) -> None:
    """Procesa todos los productos marcados NEEDS_REVIEW en el cache."""
    cache = load_cache()

    # Cargar productos que necesitan review
    last_run = DATA_DIR / "last_run_stats.json"
    if not last_run.exists():
        logger.info("No hay last_run_stats.json. Ejecuta main.py primero.")
        return

    stats = json.loads(last_run.read_text(encoding="utf-8"))
    needs_review = stats.get("needs_review", [])

    if not needs_review:
        logger.info("No hay productos marcados para revisión MusicBrainz.")
        return

    logger.info(f"Procesando {len(needs_review)} pares (artista, álbum) con MusicBrainz...")

    if limit:
        needs_review = needs_review[:limit]

    processed = 0
    found = 0
    last_request = 0.0

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for item in needs_review:
            artist = item.get("artist", "")
            album = item.get("album", "")
            key = cache_key(artist, album)

            if key in cache:
                logger.debug(f"Cache hit: {artist} — {album}")
                continue

            # Rate limiting estricto
            elapsed = time.monotonic() - last_request
            if elapsed < MB_RATE_LIMIT:
                await asyncio.sleep(MB_RATE_LIMIT - elapsed)

            result = await query_musicbrainz(client, artist, album)
            last_request = time.monotonic()
            processed += 1

            if result:
                cache[key] = result
                found += 1
                logger.info(
                    f"[{found}] '{artist}' — '{album}' → "
                    f"'{result['artist_canonical']}' — '{result['album_canonical']}' "
                    f"(score: {result['score']})"
                )
            else:
                # Guardar "no encontrado" para no volver a consultar
                cache[key] = {"artist_canonical": artist, "album_canonical": album, "mb_id": ""}
                logger.debug(f"No encontrado: {artist} — {album}")

            # Guardar cache incremental cada 50 queries
            if processed % 50 == 0:
                save_cache(cache)
                logger.info(f"Cache guardado ({processed} procesados, {found} encontrados)")

    save_cache(cache)
    logger.info(
        f"Normalización completada: {processed} consultados, "
        f"{found} encontrados en MusicBrainz. "
        f"Cache: {len(cache)} entradas."
    )


def main():
    p = argparse.ArgumentParser(description="Normalización Capa 3 (MusicBrainz)")
    p.add_argument("--limit", type=int, default=0, help="Límite de queries (0=todos)")
    args = p.parse_args()
    asyncio.run(run_normalization(args.limit))


if __name__ == "__main__":
    main()
