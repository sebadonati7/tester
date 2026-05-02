#!/usr/bin/env python3
"""
Genera documenti per ogni struttura in master_kb.json e li carica su Supabase.

Per ogni struttura, vengono creati documenti chunckizzati con embedding OpenAI
(text-embedding-3-small) pronti per la ricerca semantica via RPC
'search_documents_semantic'.

Prerequisiti:
  - Tabella 'documents' creata in Supabase (vedi SQL in questo file)
  - Variabili d'ambiente: SUPABASE_URL, SUPABASE_KEY, OPENAI_API_KEY

Utilizzo:
    python scripts/generate_info_documents_from_master_kb.py

SQL da eseguire in Supabase prima di questo script:
    CREATE EXTENSION IF NOT EXISTS vector;

    CREATE TABLE IF NOT EXISTS documents (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        title VARCHAR(255) NOT NULL,
        content TEXT NOT NULL,
        doc_type VARCHAR(50),
        location VARCHAR(100),
        embedding vector(1536),
        metadata JSONB,
        created_at TIMESTAMP DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_embedding ON documents
        USING ivfflat (embedding vector_cosine_ops);
    CREATE INDEX IF NOT EXISTS idx_location ON documents(location);
    CREATE INDEX IF NOT EXISTS idx_doc_type ON documents(doc_type);

    CREATE OR REPLACE FUNCTION search_documents_semantic(
        query_embedding vector,
        location VARCHAR DEFAULT NULL,
        top_k INT DEFAULT 3,
        threshold FLOAT DEFAULT 0.2
    )
    RETURNS TABLE (
        id UUID,
        title VARCHAR,
        content TEXT,
        doc_type VARCHAR,
        location VARCHAR,
        similarity FLOAT
    ) AS $$
    BEGIN
        RETURN QUERY
        SELECT
            d.id,
            d.title,
            d.content,
            d.doc_type,
            d.location,
            1 - (d.embedding <=> query_embedding) as similarity
        FROM documents d
        WHERE (location IS NULL OR d.location ILIKE location)
        AND (1 - (d.embedding <=> query_embedding)) > threshold
        ORDER BY d.embedding <=> query_embedding
        LIMIT top_k;
    END;
    $$ LANGUAGE plpgsql;
"""

import json
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_env(key: str) -> str:
    """Legge variabile d'ambiente con messaggio d'errore chiaro."""
    value = os.environ.get(key)
    if not value:
        logger.error(f"❌ Variabile d'ambiente mancante: {key}")
        sys.exit(1)
    return value


def embed_text(client, text: str) -> list:
    """Genera embedding OpenAI text-embedding-3-small."""
    response = client.embeddings.create(input=text, model="text-embedding-3-small")
    return response.data[0].embedding


def format_orari(orari_dict: dict) -> str:
    """Formatta orari in testo leggibile."""
    if not orari_dict:
        return "Non disponibili"
    lines = []
    for giorno, fascia in orari_dict.items():
        if giorno != "note":
            lines.append(f"- {giorno.capitalize()}: {fascia}")
    if orari_dict.get("note"):
        lines.append(f"Note: {orari_dict['note']}")
    return "\n".join(lines) if lines else "Non disponibili"


def build_facility_content(facility: dict) -> str:
    """Costruisce il testo completo di un documento struttura."""
    nome = facility.get("nome", "N/A")
    tipologia = facility.get("tipologia", "")
    comune = facility.get("comune", "")
    provincia = facility.get("provincia", "")
    servizi = ", ".join(facility.get("servizi_disponibili", []))
    orari_text = format_orari(facility.get("orari", {}))
    contatti = facility.get("contatti", {})
    telefono = contatti.get("telefono", "N/A") if isinstance(contatti, dict) else "N/A"
    indirizzo = contatti.get("indirizzo", "N/A") if isinstance(contatti, dict) else "N/A"
    web = contatti.get("web", "N/A") if isinstance(contatti, dict) else "N/A"

    return f"""STRUTTURA: {nome}
TIPO: {tipologia}
COMUNE: {comune}
PROVINCIA: {provincia}

SERVIZI: {servizi}

ORARI:
{orari_text}

CONTATTI:
Telefono: {telefono}
Indirizzo: {indirizzo}
Web: {web}"""


def generate_documents():
    """Carica strutture da master_kb.json e le inserisce come documenti in Supabase."""
    import openai
    from supabase import create_client

    supabase_url = get_env("SUPABASE_URL")
    supabase_key = get_env("SUPABASE_KEY")
    openai_api_key = get_env("OPENAI_API_KEY")

    supabase = create_client(supabase_url, supabase_key)
    openai_client = openai.OpenAI(api_key=openai_api_key)

    kb_path = Path(__file__).parent.parent / "master_kb.json"
    if not kb_path.exists():
        # Try alternate location
        kb_path = Path("master_kb.json")
    if not kb_path.exists():
        logger.error(f"❌ master_kb.json non trovato in {kb_path}")
        sys.exit(1)

    with open(kb_path, "r", encoding="utf-8") as f:
        master_kb = json.load(f)

    facilities = master_kb.get("facilities", [])
    logger.info(f"🔄 Processing {len(facilities)} strutture...")

    inserted = 0
    errors = 0

    for facility in facilities:
        nome = facility.get("nome", "Struttura sconosciuta")
        comune = facility.get("comune", "")

        content = build_facility_content(facility)
        title = f"{nome} - {comune}"

        try:
            embedding = embed_text(openai_client, content)

            supabase.table("documents").insert(
                {
                    "title": title,
                    "content": content,
                    "doc_type": "facility_complete",
                    "location": comune,
                    "embedding": embedding,
                    "metadata": {
                        "facility_id": facility.get("id"),
                        "tipologia": facility.get("tipologia"),
                    },
                }
            ).execute()

            inserted += 1
            logger.info(f"  ✓ {title}")

        except Exception as e:
            errors += 1
            logger.error(f"  ✗ {title}: {e}")

    logger.info(f"\n✅ Completato: {inserted} inseriti, {errors} errori")


if __name__ == "__main__":
    generate_documents()
