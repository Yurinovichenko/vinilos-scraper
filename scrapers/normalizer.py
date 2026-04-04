"""
normalizer.py — Normalización de nombres de artistas y álbumes.

Capa 1: Reglas deterministas locales (title case, sufijos, artículos, puntuación).
Capa 2: Deduplicación cross-store via rapidfuzz (detecta irregularidades entre tiendas).
Capa 3: MusicBrainz API — en normalize.py (job mensual separado).

El 95%+ de los casos se resuelven con Capas 1+2, sin costo de API.
"""

import re
import unicodedata
from collections import defaultdict

from rapidfuzz import fuzz, process

from scrapers.stores import ARTIST_EQUIVALENCES

# ─────────────────────────────────────────────────────────────
# CAPA 1: Normalización local determinista
# ─────────────────────────────────────────────────────────────

# Keywords que identifican un paréntesis/corchete como sufijo de edición
_EDITION_KEYWORDS = re.compile(
    r"""
    remaster(?:ed)?|
    remasterizado|
    180\s*g(?:r(?:amos?)?)?|
    limited\s+edition|
    edici[oó]n\s+limitada|
    colou?red?\s+vinyl|
    vinilo\s+colou?red?|
    (?:colou?red?\s+)?vinyl\b|
    \bvinilo\b|
    \d+\s*lp\b|
    record\s+store\s+day|
    \brsd\b|
    anniversary\s+edition|
    \d+th\s+anniversary|
    deluxe\s+edition|
    bonus\s+tracks?|
    \bexplicit\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Paréntesis que contienen solo un año (ej: "(1973)" al final)
_YEAR_PARENS = re.compile(r"\s*[\[\(]\s*\d{4}\s*[\]\)]\s*$")

def _remove_edition_suffixes(text: str) -> str:
    """
    Elimina paréntesis/corchetes al final del título que contienen
    keywords de edición. Itera hasta que no haya más cambios.
    """
    for _ in range(5):
        # Buscar el último par de paréntesis/corchetes
        m = re.search(r"\s*[\[\(][^\[\]()]*[\]\)]\s*$", text)
        if not m:
            break
        bracket_content = m.group(0)
        inner = bracket_content.strip()[1:-1]  # Contenido sin los delimitadores
        if _EDITION_KEYWORDS.search(inner):
            text = text[: m.start()].strip()
        elif re.fullmatch(r"\s*\d{4}\s*", inner):
            # Solo año
            text = text[: m.start()].strip()
        else:
            break  # El último paréntesis no es edición; parar
    return text

# Sufijos de formato fuera de paréntesis
_ALBUM_FORMAT_SUFFIX = re.compile(
    r"\s*[-–]\s*(?:\d+\s*)?(?:lp|ep|single|vinilo|vinyl)\s*$",
    re.IGNORECASE,
)

# "The Foo" / "Foo, The" → normalizar a "The Foo"
_THE_SUFFIX = re.compile(r"^(.+),\s*the\s*$", re.IGNORECASE)


def _normalize_unicode(text: str) -> str:
    """NFD → NFC para estandarizar representaciones Unicode."""
    return unicodedata.normalize("NFC", text)


def _apply_title_case(text: str) -> str:
    """
    Title case conservando artículos en minúscula en medio de la frase.
    Ej: "the dark side of the moon" → "The Dark Side of the Moon"
    """
    minor_words = {"of", "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "with", "by"}
    words = text.split()
    result = []
    for i, word in enumerate(words):
        if i == 0 or word.lower() not in minor_words:
            result.append(word.capitalize())
        else:
            result.append(word.lower())
    return " ".join(result)


def normalize_artist(raw: str) -> str:
    """
    Normaliza el nombre de un artista:
    1. Unicode NFC
    2. Strip y colapso de espacios
    3. Equivalencias configuradas (AC_DC → AC/DC, etc.)
    4. "Foo, The" → "The Foo"
    5. Title case
    """
    if not raw:
        return ""

    text = _normalize_unicode(raw.strip())
    text = re.sub(r"\s+", " ", text)  # Colapsar espacios múltiples

    # Equivalencias exactas primero (mayúsculas/minúsculas)
    for wrong, correct in ARTIST_EQUIVALENCES.items():
        if text.upper() == wrong.upper():
            return correct

    # "The Foo" / "Foo, The"
    m = _THE_SUFFIX.match(text)
    if m:
        text = f"The {m.group(1).strip()}"

    return _apply_title_case(text)


def normalize_album(raw: str) -> str:
    """
    Normaliza el título de un álbum:
    1. Unicode NFC
    2. Eliminar sufijos de edición entre paréntesis/corchetes
    3. Eliminar sufijos de formato fuera de paréntesis
    4. Strip y colapso de espacios
    5. Title case
    """
    if not raw:
        return ""

    text = _normalize_unicode(raw.strip())

    # Eliminar sufijos de edición iterativamente
    text = _remove_edition_suffixes(text)
    text = _ALBUM_FORMAT_SUFFIX.sub("", text).strip()

    text = re.sub(r"\s+", " ", text)
    return _apply_title_case(text)


# ─────────────────────────────────────────────────────────────
# CAPA 2: Deduplicación cross-store via rapidfuzz
# ─────────────────────────────────────────────────────────────

def group_duplicates(
    products: list,  # list[Product]
    threshold: int = 85,
) -> dict[tuple[str, str], list]:
    """
    Agrupa productos de distintas tiendas que son el mismo disco
    (nombres similares tras normalización Capa 1).

    Retorna: dict de (artist_canon, album_canon) → [Product, ...]
    """
    # Crear clave de búsqueda normalizada
    keyed: list[tuple[str, str, object]] = []
    for p in products:
        a = p.artist_norm or normalize_artist(p.artist)
        al = p.album_norm or normalize_album(p.album)
        keyed.append((a, al, p))

    # Agrupar por similitud
    groups: dict[tuple[str, str], list] = {}
    assigned: set[int] = set()

    for i, (a, al, p) in enumerate(keyed):
        if i in assigned:
            continue

        # Este producto es la semilla del grupo
        group_key = (a, al)
        group = [p]
        assigned.add(i)

        for j, (a2, al2, p2) in enumerate(keyed):
            if j <= i or j in assigned:
                continue
            # Comparar artista Y álbum
            artist_score = fuzz.token_sort_ratio(a.lower(), a2.lower())
            album_score = fuzz.token_sort_ratio(al.lower(), al2.lower())
            if artist_score >= threshold and album_score >= threshold:
                group.append(p2)
                assigned.add(j)

        groups[group_key] = group

    return groups


def detect_irregularities(
    products: list,  # list[Product]
    threshold: int = 85,
) -> list:
    """
    Detecta productos que aparecen en ≥2 tiendas con nombres distintos
    (después de normalización Capa 1). Los marca como needs_review=True
    para que Capa 3 (MusicBrainz) los resuelva.

    Retorna lista de productos con artist_norm/album_norm actualizados.
    """
    groups = group_duplicates(products, threshold)

    for (canon_artist, canon_album), group in groups.items():
        if len(group) < 2:
            continue

        # Elegir nombre canónico por votación de mayoría (nombre más frecuente)
        artist_votes: dict[str, int] = defaultdict(int)
        album_votes: dict[str, int] = defaultdict(int)
        for p in group:
            a = p.artist_norm or normalize_artist(p.artist)
            al = p.album_norm or normalize_album(p.album)
            artist_votes[a] += 1
            album_votes[al] += 1

        canon_a = max(artist_votes, key=artist_votes.__getitem__)
        canon_al = max(album_votes, key=album_votes.__getitem__)

        # Verificar si hay discrepancias reales (no solo mismo nombre en distintas tiendas)
        has_discrepancy = (
            len(artist_votes) > 1 or len(album_votes) > 1
        )

        for p in group:
            p.artist_norm = canon_a
            p.album_norm = canon_al
            if has_discrepancy and not p.mb_id:
                p.mb_id = "NEEDS_REVIEW"  # Señal para Capa 3

    # Productos que no pasaron por ningún grupo con ≥2 miembros: aplicar solo Capa 1
    for p in products:
        if not p.artist_norm:
            p.artist_norm = normalize_artist(p.artist)
        if not p.album_norm:
            p.album_norm = normalize_album(p.album)
        if p.mb_id == "NEEDS_REVIEW":
            pass  # Ya marcados para MusicBrainz

    return products
