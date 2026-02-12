import os
import json
import logging
import sys

# Configura il logger per vedere cosa succede
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

print("\n🚀 AVVIO SCRIPT DI INGESTIONE...")

try:
    from pathlib import Path
    from dotenv import load_dotenv
    # Carica variabili ambiente
    load_dotenv()
    
    from supabase import create_client, Client
    from langchain_google_genai import GoogleGenerativeAIEmbeddings
    from langchain_community.document_loaders import PyPDFLoader
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError as e:
    print(f"❌ ERRORE IMPORTAZIONE: {e}")
    print("Assicurati di aver fatto: pip install -r requirements_local.txt")
    sys.exit(1)

# --- CONFIGURAZIONE ---
POSSIBLE_PATHS = [
    Path("data/protocols"),
    Path("protocols"),
    Path(".") 
]

def get_protocols_dir():
    for p in POSSIBLE_PATHS:
        if p.exists() and list(p.glob("*.pdf")):
            return p
    return Path("data/protocols")

def main():
    # 1. RECUPERA CHIAVI
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

    if not SUPABASE_URL or not SUPABASE_KEY or not GEMINI_API_KEY:
        print("\n❌ ERRORE: Chiavi mancanti nel file .env!")
        print("Apri il file .env e incolla le tue chiavi di Supabase e Google.")
        return

    # 2. CONNETTI A SUPABASE
    print("🔌 Connessione a Supabase in corso...")
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print(f"❌ Errore connessione Supabase: {e}")
        return

    # 3. CONFIGURA GEMINI
    print("🧠 Configurazione Google Gemini...")
    try:
        embeddings = GoogleGenerativeAIEmbeddings(
            model="models/embedding-001",
            google_api_key=GEMINI_API_KEY
        )
    except Exception as e:
        print(f"❌ Errore Gemini: {e}")
        return

    # 4. CERCA PDF
    protocols_dir = get_protocols_dir()
    pdf_files = list(protocols_dir.glob("*.pdf"))
    
    if not pdf_files:
        print(f"\n⚠️  ATTENZIONE: Nessun PDF trovato in: {protocols_dir.absolute()}")
        print("👉 Copia i tuoi file PDF dentro la cartella 'data/protocols' e riprova.")
        return

    print(f"📚 Trovati {len(pdf_files)} PDF. Inizio elaborazione...\n")

    # 5. ELABORA
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    total_chunks = 0

    for pdf in pdf_files:
        print(f"🔄 Lettura: {pdf.name}")
        try:
            loader = PyPDFLoader(str(pdf))
            pages = loader.load()
            
            # Pulisci metadata
            for page in pages:
                page.metadata["source"] = pdf.name
                page.metadata["protocol"] = pdf.stem
                # Rimuovi dati complessi non serializzabili
                if "file_path" in page.metadata: del page.metadata["file_path"]

            chunks = text_splitter.split_documents(pages)
            if not chunks:
                print("   ⚠️ File vuoto o illeggibile.")
                continue

            # Crea embeddings
            texts = [c.page_content for c in chunks]
            print(f"   🧮 Calcolo {len(texts)} embeddings con Gemini...")
            vectors = embeddings.embed_documents(texts)

            # Prepara dati per Supabase
            records = []
            for text, vector, meta in zip(texts, vectors, chunks):
                records.append({
                    "content": text,
                    "embedding": vector,
                    "metadata": json.dumps(meta.metadata)
                })

            # Carica
            supabase.table("protocol_vectors").insert(records).execute()
            print(f"   ✅ Caricati {len(records)} chunks nel Cloud.")
            total_chunks += len(records)

        except Exception as e:
            print(f"   ❌ Errore critico su {pdf.name}: {e}")

    print("\n" + "="*50)
    print(f"🎉 COMPLETATO! Totale chunks online: {total_chunks}")
    print("="*50)

if __name__ == "__main__":
    main()