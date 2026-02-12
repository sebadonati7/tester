"""
SIRAYA - Caricamento PDF su Supabase (SENZA embeddings)
Versione 2: con pulizia caratteri non validi
"""

import os, sys, json, re
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client
import pypdf

print("\n🚀 SIRAYA - Caricamento Semplificato\n")

# Credenziali
load_dotenv()
URL = os.getenv("SUPABASE_URL")
KEY = os.getenv("SUPABASE_KEY")

if not URL or not KEY:
    print("❌ Chiavi mancanti")
    sys.exit(1)

# Connetti
print("🔌 Connessione...")
supabase = create_client(URL, KEY)
print("�� Connesso\n")

# Trova PDF
pdf_dir = Path("data/protocols")
pdfs = list(pdf_dir.glob("*.pdf")) if pdf_dir.exists() else []

if not pdfs:
    print("❌ Nessun PDF")
    sys.exit(1)

print(f"✅ {len(pdfs)} PDF trovati\n")

# Funzioni
def clean_text(text):
    """Rimuove caratteri non validi per PostgreSQL."""
    # Rimuovi caratteri NULL e altri caratteri di controllo
    text = text.replace('\x00', '')  # NULL
    text = re.sub(r'[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F]', '', text)  # Altri controlli
    return text.strip()

def load_pdf(path):
    """Carica PDF."""
    docs = []
    try:
        with open(path, 'rb') as f:
            reader = pypdf.PdfReader(f)
            for i, page in enumerate(reader.pages):
                text = page.extract_text()
                if text and len(text.strip()) > 100:
                    # Pulisci testo
                    clean = clean_text(text)
                    if clean:
                        docs.append({
                            "text": clean,
                            "source": path.name,
                            "page": i + 1,
                            "protocol": path.stem
                        })
    except Exception as e:
        print(f"   ❌ {e}")
    return docs

def split_text(text, size=1500, overlap=300):
    """Split in chunks."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunk = text[start:end]
        # Pulisci anche il chunk
        chunk = clean_text(chunk)
        if chunk:
            chunks.append(chunk)
        start = end - overlap
    return chunks

# Elabora
print("🚀 ELABORAZIONE\n")
total = 0
errors = []

for i, pdf in enumerate(pdfs, 1):
    print(f"📄 [{i}/{len(pdfs)}] {pdf.name}")
    
    try:
        pages = load_pdf(pdf)
        if not pages:
            print("   ⚠️ Vuoto\n")
            continue
        
        print(f"   ✅ {len(pages)} pagine")
        
        # Split
        chunks = []
        for page in pages:
            for chunk_text in split_text(page["text"]):
                chunks.append({
                    "content": chunk_text,
                    "source": page["source"],
                    "page": page["page"],
                    "protocol": page["protocol"]
                })
        
        if not chunks:
            print("   ⚠️ Nessun chunk valido dopo pulizia\n")
            continue
        
        print(f"   ✅ {len(chunks)} chunks")
        
        # Upload
        print("   📤 Upload...", end="", flush=True)
        
        uploaded = 0
        for b in range(0, len(chunks), 500):
            batch = chunks[b:b+500]
            try:
                response = supabase.table("protocol_chunks").insert(batch).execute()
                if response.data:
                    uploaded += len(batch)
                if b + 500 < len(chunks):
                    print(".", end="", flush=True)
            except Exception as batch_error:
                print(f" ❌ Batch error: {batch_error}")
                errors.append(f"{pdf.name} - Batch {b}-{b+500}: {batch_error}")
        
        print(f" ✅ ({uploaded}/{len(chunks)} caricati)\n")
        total += uploaded
        
    except Exception as e:
        print(f"   ❌ {e}\n")
        errors.append(f"{pdf.name}: {e}")

print("="*50)
print("🎉 COMPLETATO!")
print("="*50)
print(f"\n📊 Statistiche:")
print(f"   • PDF elaborati: {len(pdfs)}")
print(f"   • Chunks caricati: {total}")

if errors:
    print(f"\n⚠️ Errori ({len(errors)}):")
    for err in errors:
        print(f"   - {err}")

print("\n✅ Dati pronti su Supabase!\n")