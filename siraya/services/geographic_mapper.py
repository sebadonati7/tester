"""
SIRAYA Health Navigator - Geographic Mapper
RAG-Enhanced Analytics V2: Comune → Distretto AUSL con fuzzy matching.

Mappa comuni Emilia-Romagna a distretti sanitari AUSL.
Supporta fuzzy matching (Levenshtein-like) per varianti ortografiche.
"""

import json
import logging
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
MAPPING_PATH = BASE_DIR / "data" / "er_comuni_mapping.json"


def _similarity(a: str, b: str) -> float:
    """Calcola similarità 0-1 tra due stringhe (SequenceMatcher ratio)."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _load_mapping() -> Dict:
    """Carica mapping comuni da JSON."""
    try:
        with open(MAPPING_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Errore caricamento er_comuni_mapping: %s", e)
        return {}


def _build_comune_index() -> List[Tuple[str, str, str, str]]:
    """
    Costruisce indice flat: (comune_normalized, ausl, distretto, comune_originale).
    """
    mapping = _load_mapping()
    index = []
    for ausl, districts in mapping.items():
        if not isinstance(districts, dict):
            continue
        for distretto, comuni in districts.items():
            if not isinstance(comuni, list):
                continue
            for c in comuni:
                cn = c.lower().strip() if isinstance(c, str) else ""
                if cn:
                    index.append((cn, ausl, distretto, c))
    return index


# Cache indice per performance
_comune_index: Optional[List[Tuple[str, str, str, str]]] = None


def _get_index() -> List[Tuple[str, str, str, str]]:
    global _comune_index
    if _comune_index is None:
        _comune_index = _build_comune_index()
    return _comune_index


def map_comune_to_district(
    location_str: Optional[str],
    fuzzy_threshold: float = 0.80,
) -> Dict[str, Any]:
    """
    Mappa comune/location string → distretto sanitario AUSL.

    Args:
        location_str: Stringa comune o location (es. "Bologna", "Castel S. Pietro")
        fuzzy_threshold: Soglia similarità per fuzzy match (0.0-1.0, default 0.80)

    Returns:
        {
            'ausl': str,           # Es. "AUSL_Bologna"
            'distretto': str,       # Es. "Bologna_Citta"
            'comune_matched': str,  # Comune esatto matchato
            'confidence': float,    # 0.0-1.0
            'match_type': str      # 'exact'|'fuzzy'|'unknown'
        }
    """
    result = {
        "ausl": "UNKNOWN",
        "distretto": "N/A",
        "comune_matched": "",
        "confidence": 0.0,
        "match_type": "unknown",
    }

    if not location_str or not str(location_str).strip():
        return result

    loc = str(location_str).strip().lower()
    # Normalizza varianti comuni
    loc = loc.replace("’", "'").replace("`", "'")
    loc_clean = loc.replace(".", " ").replace("-", " ").replace("  ", " ").strip()

    index = _get_index()
    if not index:
        return result

    # Exact match
    for comune_norm, ausl, distretto, comune_orig in index:
        if comune_norm == loc or comune_norm == loc_clean:
            result["ausl"] = ausl
            result["distretto"] = distretto
            result["comune_matched"] = comune_orig
            result["confidence"] = 1.0
            result["match_type"] = "exact"
            return result

    # Fuzzy match
    best: Optional[Tuple[float, str, str, str, str]] = None
    for comune_norm, ausl, distretto, comune_orig in index:
        sim = _similarity(loc, comune_norm)
        if sim >= fuzzy_threshold:
            if best is None or sim > best[0]:
                best = (sim, ausl, distretto, comune_orig, "fuzzy")
    if best:
        sim, ausl, distretto, comune_orig, mtype = best
        result["ausl"] = ausl
        result["distretto"] = distretto
        result["comune_matched"] = comune_orig
        result["confidence"] = round(sim, 2)
        result["match_type"] = mtype
        return result

    # Fuori regione (comuni noti italiani ma non ER)
    extra_regione = ["milano", "roma", "napoli", "torino", "firenze", "genova", "venezia", "padova"]
    if loc in extra_regione or any(loc.startswith(x) for x in extra_regione):
        result["ausl"] = "EXTRA_REGIONE"
        result["distretto"] = "N/A"
        result["match_type"] = "unknown"
        result["confidence"] = 0.0
        return result

    return result


def get_available_districts() -> List[str]:
    """Restituisce lista distretti disponibili per filtri UI."""
    mapping = _load_mapping()
    districts = []
    for ausl, dists in mapping.items():
        if isinstance(dists, dict):
            for d in dists.keys():
                districts.append(f"{ausl} - {d}")
    return sorted(districts)


def invalidate_cache() -> None:
    """Invalida cache indice comuni (dopo aggiornamento mapping)."""
    global _comune_index
    _comune_index = None
