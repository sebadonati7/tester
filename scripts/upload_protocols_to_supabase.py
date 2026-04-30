"""
Upload mental health protocols to Supabase (mental_health_protocols table).

Legge protocolli JSON da knowledge_base/mental_health_protocols/,
divide in chunk logici e fa upload su Supabase.

Schema Supabase:
    CREATE TABLE mental_health_protocols (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        protocol_type VARCHAR(50),
        chunk_order INT,
        chunk_content TEXT,
        metadata JSONB,
        risk_stratification JSONB,
        created_at TIMESTAMP DEFAULT NOW()
    );

Uso:
    python scripts/upload_protocols_to_supabase.py
    python scripts/upload_protocols_to_supabase.py --dry-run
    python scripts/upload_protocols_to_supabase.py --protocol suicidal_ideation
"""

import os
import sys
import json
import argparse
import logging
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ── Paths ──
REPO_ROOT = Path(__file__).resolve().parent.parent
KB_DIR = REPO_ROOT / "knowledge_base" / "mental_health_protocols"

# ── Embedded protocols (usati se nessun file JSON trovato) ──
EMBEDDED_PROTOCOLS: Dict[str, Dict[str, Any]] = {
    "suicidal_ideation": {
        "name": "ASQ - Ask Suicide Screening Questions",
        "chunks": [
            {
                "order": 0,
                "content": (
                    "SCREENING INIZIALE — Ideazione Suicidaria\n\n"
                    "Domanda 1 (ASQ): Hai avuto pensieri di volerti fare del male o di toglierti la vita?\n"
                    "→ Se NO: basso rischio, documentare e monitorare\n"
                    "→ Se SÌ: procedere con domanda 2"
                ),
                "metadata": {"phase": "screening", "protocol": "ASQ"},
            },
            {
                "order": 1,
                "content": (
                    "VALUTAZIONE PIANO — Ideazione Suicidaria\n\n"
                    "Domanda 2 (ASQ): Hai un piano su come farlo?\n"
                    "→ Se NO (solo ideazione): rischio moderato\n"
                    "→ Se SÌ: valutare accesso ai mezzi (domanda 3)"
                ),
                "metadata": {"phase": "assessment", "protocol": "ASQ"},
            },
            {
                "order": 2,
                "content": (
                    "VALUTAZIONE MEZZI — Ideazione Suicidaria\n\n"
                    "Domanda 3 (ASQ): Hai accesso ai mezzi per attuare questo piano?\n"
                    "→ Se NO: rischio alto ma non immediato → Crisis Center\n"
                    "→ Se SÌ: rischio IMMEDIATO → 118 + SPDC"
                ),
                "metadata": {"phase": "means_assessment", "protocol": "ASQ"},
            },
            {
                "order": 3,
                "content": (
                    "STORIA E SUPPORTO — Ideazione Suicidaria\n\n"
                    "Domanda 4: Hai già tentato di toglierti la vita in passato?\n"
                    "Domanda 5: Hai qualcuno vicino a te in questo momento?\n"
                    "→ Tentativo pregresso: fattore di rischio significativo\n"
                    "→ Isolamento: aumenta urgenza"
                ),
                "metadata": {"phase": "history_support", "protocol": "ASQ"},
            },
        ],
        "risk_stratification": {
            "immediate": {
                "description": "Piano concreto + accesso ai mezzi",
                "facility": "SPDC + 118",
                "action": "emergenza"
            },
            "high": {
                "description": "Ideazione attiva senza piano o senza mezzi",
                "facility": "Crisis Center",
                "action": "urgente"
            },
            "moderate": {
                "description": "Pensieri passivi di morte senza ideazione attiva",
                "facility": "CSM",
                "action": "consulenza"
            },
            "low": {
                "description": "Nessuna ideazione attiva",
                "facility": "MMG",
                "action": "follow-up"
            }
        }
    },
    "depression": {
        "name": "PHQ-9 - Patient Health Questionnaire-9",
        "chunks": [
            {
                "order": 0,
                "content": (
                    "PHQ-9 SCREENING — Depressione (Ultime 2 settimane)\n\n"
                    "Scala: 0=Mai, 1=Qualche giorno, 2=Più di metà dei giorni, 3=Quasi ogni giorno\n\n"
                    "Domanda 1: Poco interesse o piacere nel fare le cose?\n"
                    "Domanda 2: Sentirsi giù di morale, depresso o senza speranza?"
                ),
                "metadata": {"phase": "screening", "protocol": "PHQ-9", "items": [1, 2]},
            },
            {
                "order": 1,
                "content": (
                    "PHQ-9 SINTOMI NEUROVEGETATIVI\n\n"
                    "Domanda 3: Difficoltà ad addormentarsi o dormire troppo?\n"
                    "Domanda 4: Sentirsi stanco/a o avere poca energia?\n"
                    "Domanda 5: Poco appetito o mangiare troppo?"
                ),
                "metadata": {"phase": "neurovegetative", "protocol": "PHQ-9", "items": [3, 4, 5]},
            },
            {
                "order": 2,
                "content": (
                    "PHQ-9 SINTOMI COGNITIVI\n\n"
                    "Domanda 6: Sentirsi in colpa o pensare di non valere niente?\n"
                    "Domanda 7: Difficoltà a concentrarsi (leggere, guardare la TV, ecc.)?\n"
                    "Domanda 8: Muoversi/parlare così lentamente da essere notato, "
                    "o essere agitato/irrequieto?"
                ),
                "metadata": {"phase": "cognitive", "protocol": "PHQ-9", "items": [6, 7, 8]},
            },
            {
                "order": 3,
                "content": (
                    "PHQ-9 DOMANDA CRITICA\n\n"
                    "Domanda 9 (CRITICA): Pensieri di essere meglio morto/a "
                    "o di farti del male in qualche modo?\n"
                    "→ Qualsiasi risposta positiva: richiede valutazione immediata rischio suicidario\n"
                    "Calcolo score: somma punteggi 1-9\n"
                    "< 10: lieve | 10-19: moderato | ≥ 20: grave"
                ),
                "metadata": {"phase": "critical_item", "protocol": "PHQ-9", "items": [9]},
            },
        ],
        "risk_stratification": {
            "high": {
                "score_threshold": 20,
                "facility": "CSM urgente",
                "description": "Depressione grave"
            },
            "moderate": {
                "score_threshold": 10,
                "facility": "CSM",
                "description": "Depressione moderata"
            },
            "low": {
                "score_threshold": 0,
                "facility": "MMG + follow-up",
                "description": "Depressione lieve"
            }
        }
    },
    "eating_disorder": {
        "name": "DCA - Disturbi del Comportamento Alimentare",
        "chunks": [
            {
                "order": 0,
                "content": (
                    "DCA SCREENING INIZIALE\n\n"
                    "Domanda 1: Puoi descrivermi il tuo rapporto con il cibo in questo periodo?\n"
                    "Domanda 2: Hai notato cambiamenti significativi nel tuo peso negli ultimi mesi?"
                ),
                "metadata": {"phase": "screening", "protocol": "DCA"},
            },
            {
                "order": 1,
                "content": (
                    "DCA COMPORTAMENTI ALIMENTARI\n\n"
                    "Domanda 3: Come ti senti rispetto al tuo corpo e al tuo peso?\n"
                    "Domanda 4: Hai mai evitato di mangiare per paura di ingrassare?"
                ),
                "metadata": {"phase": "behavior", "protocol": "DCA"},
            },
            {
                "order": 2,
                "content": (
                    "DCA PURGING E CONTROLLO\n\n"
                    "Domanda 5: Hai mai vomitato intenzionalmente dopo aver mangiato?\n"
                    "Domanda 6: Usi lassativi, diuretici o esercizio fisico eccessivo "
                    "per controllare il peso?\n"
                    "→ Comportamenti compensatori: indica bulimia nervosa o DCA misto"
                ),
                "metadata": {"phase": "purging", "protocol": "DCA"},
            },
        ],
        "risk_stratification": {
            "default": {
                "facility": "Centro DCA",
                "description": "Disturbo del comportamento alimentare — valutazione specialistica"
            }
        }
    },
    "substance_abuse": {
        "name": "SERT - Screening Dipendenze da Sostanze",
        "chunks": [
            {
                "order": 0,
                "content": (
                    "SERT SCREENING INIZIALE\n\n"
                    "Domanda 1: Quali sostanze stai assumendo e con quale frequenza?\n"
                    "Domanda 2: Da quanto tempo fai uso di queste sostanze?"
                ),
                "metadata": {"phase": "screening", "protocol": "SERT"},
            },
            {
                "order": 1,
                "content": (
                    "SERT IMPATTO E DIPENDENZA\n\n"
                    "Domanda 3: Hai già provato a smettere? Come è andata?\n"
                    "Domanda 4: L'uso di sostanze interferisce con la tua vita "
                    "quotidiana (lavoro, famiglia, relazioni)?"
                ),
                "metadata": {"phase": "impact", "protocol": "SERT"},
            },
            {
                "order": 2,
                "content": (
                    "SERT VALUTAZIONE URGENZA\n\n"
                    "Domanda 5: Stai vivendo sintomi di astinenza?\n"
                    "→ Astinenza da alcool/benzodiazepine: potenzialmente pericolosa → PS\n"
                    "→ Astinenza da oppioidi: molto dolorosa → SERT urgente\n"
                    "Raccomandata presa in carico presso il Servizio per le Dipendenze (SerD/SERT)"
                ),
                "metadata": {"phase": "urgency", "protocol": "SERT"},
            },
        ],
        "risk_stratification": {
            "default": {
                "facility": "SERT/SerD",
                "description": "Dipendenza da sostanze — Servizio per le Dipendenze"
            }
        }
    }
}


def create_supabase_client():
    """Crea client Supabase dalle variabili d'ambiente o Streamlit secrets."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")

    if not url or not key:
        try:
            import streamlit as st
            supabase_cfg = st.secrets.get("supabase", {})
            url = url or supabase_cfg.get("url")
            key = key or supabase_cfg.get("key")
        except Exception:
            pass

    if not url or not key:
        logger.error("❌ SUPABASE_URL e SUPABASE_KEY richiesti (env vars o Streamlit secrets)")
        sys.exit(1)

    from supabase import create_client
    return create_client(url, key)


def load_protocols_from_files() -> Dict[str, Any]:
    """Carica protocolli da knowledge_base/mental_health_protocols/ (se esistono)."""
    if not KB_DIR.exists():
        logger.info(f"📁 Directory {KB_DIR} non trovata. Uso protocolli embedded.")
        return {}

    protocols = {}
    for json_file in KB_DIR.glob("*.json"):
        try:
            with open(json_file, encoding="utf-8") as f:
                data = json.load(f)
            protocol_type = json_file.stem  # nome file senza estensione
            protocols[protocol_type] = data
            logger.info(f"✅ Caricato protocollo: {protocol_type} ({json_file.name})")
        except Exception as e:
            logger.warning(f"⚠️ Errore caricando {json_file}: {e}")

    return protocols


def build_supabase_rows(protocol_type: str, protocol_data: Dict) -> List[Dict]:
    """Converte protocollo in righe pronte per Supabase."""
    rows = []
    chunks = protocol_data.get("chunks", [])
    risk_strat = protocol_data.get("risk_stratification", {})

    for chunk in chunks:
        row = {
            "id": str(uuid.uuid4()),
            "protocol_type": protocol_type,
            "chunk_order": chunk.get("order", chunk.get("chunk_order", 0)),
            "chunk_content": chunk.get("content", chunk.get("chunk_content", "")),
            "metadata": json.dumps(chunk.get("metadata", {})),
            "risk_stratification": json.dumps(risk_strat),
        }
        rows.append(row)

    return rows


def upload_protocol(
    supabase,
    protocol_type: str,
    protocol_data: Dict,
    dry_run: bool = False,
) -> int:
    """Fa upload di un protocollo su Supabase. Ritorna numero di righe caricate."""
    rows = build_supabase_rows(protocol_type, protocol_data)
    protocol_name = protocol_data.get("name", protocol_type)

    logger.info(f"📤 Uploading '{protocol_name}' ({len(rows)} chunks)...")

    if dry_run:
        for row in rows:
            logger.info(f"  [DRY-RUN] chunk {row['chunk_order']}: {row['chunk_content'][:60]}...")
        return len(rows)

    # Elimina righe esistenti per questo protocol_type
    try:
        supabase.table("mental_health_protocols") \
            .delete() \
            .eq("protocol_type", protocol_type) \
            .execute()
        logger.info(f"  🗑️  Righe esistenti per '{protocol_type}' eliminate")
    except Exception as e:
        logger.warning(f"  ⚠️ Non riuscito a eliminare righe esistenti: {e}")

    # Insert nuove righe
    try:
        supabase.table("mental_health_protocols").insert(rows).execute()
        logger.info(f"  ✅ {len(rows)} chunks caricati per '{protocol_type}'")
        return len(rows)
    except Exception as e:
        logger.error(f"  ❌ Upload fallito per '{protocol_type}': {e}")
        return 0


def main():
    parser = argparse.ArgumentParser(description="Upload mental health protocols to Supabase")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Mostra le righe senza fare upload reale"
    )
    parser.add_argument(
        "--protocol", type=str, default=None,
        help="Carica solo questo protocollo (es: suicidal_ideation)"
    )
    args = parser.parse_args()

    if args.dry_run:
        logger.info("🔍 DRY-RUN: nessun upload reale verrà effettuato")
        supabase = None
    else:
        supabase = create_supabase_client()
        logger.info("✅ Connessione Supabase OK")

    # Carica protocolli (file > embedded)
    file_protocols = load_protocols_from_files()
    all_protocols = {**EMBEDDED_PROTOCOLS, **file_protocols}

    # Filtra per protocollo specifico se richiesto
    if args.protocol:
        if args.protocol not in all_protocols:
            logger.error(f"❌ Protocollo '{args.protocol}' non trovato. Disponibili: {list(all_protocols.keys())}")
            sys.exit(1)
        all_protocols = {args.protocol: all_protocols[args.protocol]}

    total_rows = 0
    for protocol_type, protocol_data in all_protocols.items():
        rows = upload_protocol(supabase, protocol_type, protocol_data, dry_run=args.dry_run)
        total_rows += rows

    logger.info(f"\n✅ Upload completato: {total_rows} righe totali per {len(all_protocols)} protocolli")


if __name__ == "__main__":
    main()
