"""
SIRAYA Health Navigator - Metadata Parser
RAG-Enhanced Analytics V2: Robust multi-format JSON parser for Supabase metadata.

Handles:
- JSON standard (double quotes)
- Dict Python nativi
- JSON malformati con apici singoli
- Nested objects (metadata.triage.urgency)
- Null/empty handling con fallback progressivo
"""

import json
import re
import logging
from typing import Dict, Any, List, Union

logger = logging.getLogger(__name__)

# Schema normalizzato output
OUTPUT_SCHEMA = {
    "urgenza": int,
    "red_flags": List[str],
    "sintomi": List[str],
    "comune": str,
    "specializzazione": str,
}


def _default_metadata() -> Dict[str, Any]:
    """Valori safe di default per metadata non parsabile."""
    return {
        "urgenza": 3,
        "red_flags": [],
        "sintomi": [],
        "comune": "",
        "specializzazione": "Generale",
    }


# Chiavi alternative per normalizzazione
URGENCY_KEYS = ["urgency", "urgenza", "triage_code", "codice_urgenza"]
RED_FLAGS_KEYS = ["red_flags", "flags", "alert_symptoms", "red_flags_detected"]
SINTOMI_KEYS = ["symptoms", "sintomi", "complaints", "sintomi_rilevati"]
COMUNE_KEYS = ["location", "comune", "city", "distretto", "municipio"]
SPECIALIZZAZIONE_KEYS = ["specialization", "department", "specialty", "specializzazione"]


def _extract_value(d: Dict, key_list: List[str], default: Any) -> Any:
    """Estrae valore da dict usando lista di chiavi alternative."""
    if not isinstance(d, dict):
        return default
    for k in key_list:
        v = d.get(k)
        if v is not None and v != "":
            return v
    return default


def _ensure_list(val: Any) -> List[str]:
    """Converte valore in lista di stringhe."""
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if x]
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return []
        if val.startswith("[") or val.startswith("("):
            try:
                parsed = json.loads(val.replace("'", '"'))
                return _ensure_list(parsed)
            except Exception:
                return [val] if val else []
        return [val]
    return [str(val)]


def _ensure_int(val: Any, lo: int = 1, hi: int = 5) -> int:
    """Converte valore in int con bounds urgenza 1-5."""
    if val is None:
        return 3
    try:
        n = int(float(val))
        return max(lo, min(hi, n))
    except (ValueError, TypeError):
        return 3


def _ensure_str(val: Any) -> str:
    """Converte valore in stringa."""
    if val is None:
        return ""
    return str(val).strip()


def _flatten_nested(obj: Any, depth: int = 0, max_depth: int = 5) -> Dict:
    """
    Appiattisce oggetto nested cercando chiavi note.
    Es: {"metadata": {"triage": {"urgency": 4}}} -> {"urgency": 4}
    """
    if depth > max_depth:
        return {}
    if isinstance(obj, dict):
        flat = {}
        for k, v in obj.items():
            if isinstance(v, dict):
                nested = _flatten_nested(v, depth + 1, max_depth)
                flat.update(nested)
            else:
                flat[k] = v
        return flat
    return {}


def _try_json_parse(s: str) -> Optional[Dict]:
    """Tenta parsing JSON con varianti (apici singoli, ecc.)."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    if not s or s in ("null", "{}", "[]"):
        return {}
    # Standard JSON
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # Apici singoli -> doppi per json.loads
    try:
        fixed = re.sub(r"'([^']*)'", r'"\1"', s)
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    # ast.literal_eval (solo per dict Python-like)
    try:
        import ast
        parsed = ast.literal_eval(s)
        if isinstance(parsed, dict):
            return parsed
    except (ValueError, SyntaxError):
        pass
    # Regex estrazione urgency
    m = re.search(r'"urgency"\s*:\s*(\d)', s)
    if m:
        return {"urgency": int(m.group(1))}
    m = re.search(r"'urgency'\s*:\s*(\d)", s)
    if m:
        return {"urgency": int(m.group(1))}
    m = re.search(r'"urgenza"\s*:\s*(\d)', s)
    if m:
        return {"urgenza": int(m.group(1))}
    return None


def parse_metadata_enhanced(metadata_raw: Any) -> Dict[str, Any]:
    """
    Parser multi-formato per metadata provenienti da Supabase.

    Gestisce:
    - JSON standard: '{"urgency": 4, "red_flags": ["chest_pain"]}'
    - Dict nativi: {'urgenza': 3, 'sintomi': ['febbre']}
    - JSON apici singoli: "{'red_flags': ['dyspnea']}"
    - Nested: {"metadata": {"triage": {"code": 4}}}
    - None, "", "null" -> default

    Returns:
        Dict normalizzato con OUTPUT_SCHEMA
    """
    result = _default_metadata()
    obj: Optional[Dict] = None

    if metadata_raw is None or (isinstance(metadata_raw, str) and not metadata_raw.strip()):
        return result

    if isinstance(metadata_raw, dict):
        obj = metadata_raw
    elif isinstance(metadata_raw, str):
        obj = _try_json_parse(metadata_raw)
        if obj is None:
            logger.debug("Metadata non parsabile come JSON: %s", metadata_raw[:200])
            return result

    if not obj:
        return result

    # Flatten nested
    flat = _flatten_nested(obj)
    if flat:
        obj = flat

    # Normalizzazione
    u = _extract_value(obj, URGENCY_KEYS, 3)
    result["urgenza"] = _ensure_int(u)

    rf = _extract_value(obj, RED_FLAGS_KEYS, [])
    result["red_flags"] = _ensure_list(rf)

    s = _extract_value(obj, SINTOMI_KEYS, [])
    result["sintomi"] = _ensure_list(s)

    c = _extract_value(obj, COMUNE_KEYS, "")
    result["comune"] = _ensure_str(c)

    sp = _extract_value(obj, SPECIALIZZAZIONE_KEYS, "Generale")
    result["specializzazione"] = _ensure_str(sp) or "Generale"

    return result


def get_parsing_success_rate(records: List[Dict], metadata_key: str = "metadata") -> float:
    """
    Calcola tasso di successo parsing su lista record.

    Args:
        records: Lista di record con campo metadata
        metadata_key: Nome campo metadata

    Returns:
        Float 0.0-1.0
    """
    if not records:
        return 0.0
    success = 0
    for r in records:
        if not isinstance(r, dict):
            continue
        raw = r.get(metadata_key)
        parsed = parse_metadata_enhanced(raw)
        if parsed["urgenza"] != 3 or parsed["red_flags"] or parsed["comune"]:
            success += 1
        elif raw and raw not in (None, "", "{}", "null"):
            success += 1
    return success / len(records) if records else 0.0
