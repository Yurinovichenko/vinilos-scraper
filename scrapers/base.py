"""
base.py — BaseScraper con retry diferenciado por tipo de error y semáforo por dominio.

Comportamiento por tipo de excepción:
  ConnectError     → 1 reintento inmediato → DOWN (no más intentos)
  TimeoutException → 3 reintentos con backoff [5s, 15s, 45s]
  HTTP 429         → Lee Retry-After, espera, 2 reintentos máx
  HTTP 403         → Rota User-Agent, +10s delay, 2 reintentos máx → BLOCKED
  HTTP 503/502     → 2 reintentos con backoff [30s, 60s]
  ParsingError     → Acumula failure_rate; >20% = STRUCTURE_CHANGED; <5% = DATA_OUTLIER
"""

import asyncio
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from scrapers.stores import StoreConfig

logger = logging.getLogger(__name__)

# Pool de User-Agents para rotación
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
]

# Semáforos por dominio (compartidos globalmente entre instancias)
_domain_semaphores: dict[str, asyncio.Semaphore] = {}


def get_semaphore(domain: str, concurrency: int) -> asyncio.Semaphore:
    """Retorna (o crea) el semáforo para un dominio dado."""
    if domain not in _domain_semaphores:
        _domain_semaphores[domain] = asyncio.Semaphore(concurrency)
    return _domain_semaphores[domain]


class ScrapeError(Exception):
    """Error de scraping con clasificación de tipo y capacidad de retry."""

    def __init__(
        self,
        store_name: str,
        error_type: str,
        message: str,
        traceback: str = "",
        timestamp: str = "",
        failure_rate: float = 0.0,
        recoverable: bool = True,
    ):
        super().__init__(message)
        self.store_name = store_name
        self.error_type = error_type  # DOWN | TIMEOUT | BLOCKED | RATE_LIMITED | STRUCTURE_CHANGED | DATA_OUTLIER | UNKNOWN
        self.message = message
        self.traceback = traceback
        self.timestamp = timestamp
        self.failure_rate = failure_rate
        self.recoverable = recoverable

    def to_dict(self) -> dict:
        return {
            "store": self.store_name,
            "error_type": self.error_type,
            "message": self.message,
            "traceback": self.traceback,
            "timestamp": self.timestamp,
            "failure_rate": self.failure_rate,
            "recoverable": self.recoverable,
        }


@dataclass
class Product:
    artist: str
    album: str
    price: int          # CLP (entero)
    available: bool
    url: str
    store: str
    # Campos de normalización (rellenados por normalize.py)
    artist_norm: str = ""
    album_norm: str = ""
    mb_id: str = ""     # MusicBrainz release ID

    def to_dict(self) -> dict:
        return {
            "artist": self.artist,
            "album": self.album,
            "price": self.price,
            "available": self.available,
            "url": self.url,
            "store": self.store,
            "artist_norm": self.artist_norm,
            "album_norm": self.album_norm,
            "mb_id": self.mb_id,
        }


class BaseScraper:
    """
    Clase base para todos los scrapers. Provee:
    - Cliente HTTP async con User-Agent rotation
    - Semáforo por dominio para limitar concurrencia
    - Retry diferenciado por tipo de error
    - Tracking de failure_rate para detección de cambios de estructura
    """

    def __init__(self, store: StoreConfig):
        self.store = store
        self.domain = urlparse(store.base_url).netloc
        self._ua_index = random.randint(0, len(USER_AGENTS) - 1)
        self._parse_attempts = 0
        self._parse_failures = 0

    def _next_ua(self) -> str:
        self._ua_index = (self._ua_index + 1) % len(USER_AGENTS)
        return USER_AGENTS[self._ua_index]

    def _headers(self) -> dict[str, str]:
        # NO incluir Accept-Encoding: httpx gestiona compresión internamente.
        # Si se especifica manualmente, httpx no descomprime la respuesta.
        return {
            "User-Agent": USER_AGENTS[self._ua_index],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-CL,es;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
        }

    async def _delay(self, extra: float = 0.0) -> None:
        wait = random.uniform(self.store.delay_min, self.store.delay_max) + extra
        await asyncio.sleep(wait)

    async def fetch(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: Optional[dict] = None,
        headers_override: Optional[dict] = None,
        timeout: float = 30.0,
    ) -> httpx.Response:
        """
        GET con retry diferenciado por tipo de error.
        Levanta ScrapeError en caso de fallo definitivo.
        """
        semaphore = get_semaphore(self.domain, self.store.concurrency)
        headers = {**self._headers(), **(headers_override or {})}

        # --- ConnectError: 1 reintento inmediato ---
        for attempt in range(2):
            try:
                async with semaphore:
                    resp = await client.get(url, params=params, headers=headers, timeout=timeout)

                # HTTP 429: Rate limit
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 60))
                    logger.warning(f"[{self.store.name}] 429 rate limit en {url}. Esperando {retry_after}s")
                    for _ in range(2):
                        await asyncio.sleep(retry_after)
                        async with semaphore:
                            resp = await client.get(url, params=params, headers=headers, timeout=timeout)
                        if resp.status_code != 429:
                            break
                    else:
                        raise ScrapeError(
                            self.store.name, "RATE_LIMITED",
                            f"429 persistente en {url} tras 2 reintentos",
                            recoverable=False,
                        )

                # HTTP 403: Bloqueado
                if resp.status_code == 403:
                    logger.warning(f"[{self.store.name}] 403 en {url}. Rotando UA y esperando 10s")
                    headers["User-Agent"] = self._next_ua()
                    await asyncio.sleep(10)
                    for _ in range(2):
                        async with semaphore:
                            resp = await client.get(url, params=params, headers=headers, timeout=timeout)
                        if resp.status_code != 403:
                            break
                    else:
                        raise ScrapeError(
                            self.store.name, "BLOCKED",
                            f"403 persistente en {url}",
                            recoverable=False,
                        )

                # HTTP 502/503: Servidor caído temporalmente
                if resp.status_code in (502, 503):
                    for wait in (30, 60):
                        logger.warning(f"[{self.store.name}] {resp.status_code} en {url}. Esperando {wait}s")
                        await asyncio.sleep(wait)
                        async with semaphore:
                            resp = await client.get(url, params=params, headers=headers, timeout=timeout)
                        if resp.status_code not in (502, 503):
                            break

                resp.raise_for_status()
                return resp

            except httpx.ConnectError as e:
                if attempt == 0:
                    logger.warning(f"[{self.store.name}] ConnectError en {url}. Reintento inmediato.")
                    await asyncio.sleep(2)
                    continue
                raise ScrapeError(
                    self.store.name, "DOWN",
                    f"ConnectError persistente: {e}",
                    recoverable=False,
                ) from e

        # No debería llegar aquí
        raise ScrapeError(self.store.name, "UNKNOWN", f"Fallo inesperado en {url}")

    async def fetch_with_timeout_retry(
        self,
        client: httpx.AsyncClient,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        """GET con reintentos en TimeoutException: backoff [5s, 15s, 45s]."""
        for wait in [0, 5, 15, 45]:
            try:
                if wait > 0:
                    logger.warning(f"[{self.store.name}] Timeout en {url}. Esperando {wait}s")
                    await asyncio.sleep(wait)
                return await self.fetch(client, url, **kwargs)
            except httpx.TimeoutException:
                continue
        raise ScrapeError(
            self.store.name, "TIMEOUT",
            f"TimeoutException persistente en {url} tras 3 reintentos",
            recoverable=True,
        )

    def record_parse_attempt(self, success: bool) -> None:
        """Registra un intento de parseo para calcular failure_rate."""
        self._parse_attempts += 1
        if not success:
            self._parse_failures += 1

    @property
    def failure_rate(self) -> float:
        if self._parse_attempts == 0:
            return 0.0
        return self._parse_failures / self._parse_attempts

    def check_structure_change(self) -> Optional[str]:
        """
        Evalúa si el failure_rate indica cambio de estructura o solo outliers.
        Retorna tipo de problema o None si todo está OK.
        """
        rate = self.failure_rate
        if self._parse_attempts < 5:
            return None  # Muestra insuficiente
        if rate > 0.20:
            return "STRUCTURE_CHANGED"
        if rate > 0.05:
            return "DATA_OUTLIER"
        return None

    async def scrape(self) -> tuple[list[Product], Optional[ScrapeError]]:
        """
        Método principal a implementar en cada subclase.
        Retorna (productos, error_o_None).
        """
        raise NotImplementedError

    @staticmethod
    def parse_price(raw: str) -> int:
        """Extrae precio entero en CLP desde strings como '$39.900', '$ 24.900 CLP'."""
        digits = re.sub(r"[^\d]", "", raw)
        return int(digits) if digits else 0
