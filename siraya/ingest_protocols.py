import os
import json
import logging
from pathlib import Path
from dotenv import load_dotenv

# Carica le variabili d'ambiente dal file .env
load_dotenv()

# Configurazione Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# Controllo Librerie
try:
    from supabase import create_client, Client
    from langchain_google_genai import GoogleGenerativeAIEmbeddings
    from langchain_community.document_loaders import PyPDFLoader
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError as e:
    logger.error(f"❌ MANCANO DELLE LIBRERIE: {e}")
    logger.error("Esegui: pip install -r requirements_local.txt")
    exit(1)

# --- CONFIGURAZIONE ---
# Cerchiamo i protocolli in vari percorsi comuni
POSSIBLE_PROTOCOL_PATHS = [
    Path("data/protocols"),
    Path("protocols"),
    Path("siraya/data/protocols"),
    Path(".") # Cerca nella root se non trova altro
]

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200

def get_protocols_dir() -> Path:
    """Trova la cartella dei protocolli o ne crea una."""
    for path in POSSIBLE_PROTOCOL_PATHS:
        if path.exists() and path.is_dir():
            # Controlla se ci sono PDF dentro
            if list(path.glob("*.pdf")):
                return path
    
    # Se non trova nulla, usa il default
    default_path = Path("data/protocols")
    default_path.mkdir(parents=True, exist_ok=True)
    return default_path

def ingest_protocols():
    print("\n🚀 AVVIO INGESTIONE SUPABASE (Google Embeddings)...")
    
    # 1. Recupera Credenziali
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

    if not SUPABASE_URL or not SUPABASE_KEY or "tuo-progetto" in str(SUPABASE_URL):
        logger.error("❌ ERRORE: Devi configurare il file .env con le tue chiavi VERE!")
        return

    # 2. Connessione Client
    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("✅ Connesso a Supabase")
    except Exception as e:
        logger.error(f"❌ Errore connessione Supabase: {e}")
        return

    # 3. Embeddings Model
    try:
        embeddings = GoogleGenerativeAIEmbeddings(
            model="models/embedding-001",
            google_api_key=GEMINI_API_KEY
        )
        logger.info("✅ Inizializzato Gemini Embeddings")
    except Exception as e:
        logger.error(f"❌ Errore Gemini: {e}")
        return

    # 4. Caricamento PDF
    protocols_dir = get_protocols_dir()
    pdf_files = list(protocols_dir.glob("*.pdf"))
    
    if not pdf_files:
        logger.warning(f"⚠️ NESSUN PDF TROVATO IN: {protocols_dir.absolute()}")
        logger.warning("👉 Copia i tuoi file PDF in quella cartella e riprova.")
        return

    logger.info(f"📚 Trovati {len(pdf_files)} PDF da processare.")
    
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE, 
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", "! ", "? ", "; ", ", ", " "]
    )

    total_chunks = 0

    for pdf_path in pdf_files:
        logger.info(f"🔄 Processando: {pdf_path.name}")
        try:
            loader = PyPDFLoader(str(pdf_path))
            pages = loader.load()
            
            # Metadata cleaning
            for page in pages:
                page.metadata["source"] = pdf_path.name
                page.metadata["protocol"] = pdf_path.stem

            chunks = text_splitter.split_documents(pages)
            texts = [c.page_content for c in chunks]
            metadatas = [c.metadata for c in chunks]

            if texts:
                logger.info(f"   🧮 Calcolo {len(texts)} embeddings...")
                vectors = embeddings.embed_documents(texts)
                
                records = []
                for text, vector, meta in zip(texts, vectors, metadatas):
                    records.append({
                        "content": text,
                        "embedding": vector,
                        "metadata": json.dumps(meta)
                    })
                
                # Batch upload
                logger.info("   ☁️  Upload su Supabase...")
                supabase.table("protocol_vectors").insert(records).execute()
                logger.info(f"   ✅ Caricati {len(records)} chunks.")
                total_chunks += len(records)
                
        except Exception as e:
            logger.error(f"   ❌ Errore critico su {pdf_path.name}: {e}")

    logger.info("-" * 40)
    logger.info(f"🎉 COMPLETATO! Totale chunks nel cloud: {total_chunks}")
    logger.info("Ora puoi aggiornare 'requirements.txt' per Streamlit Cloud e fare il deploy.")

if __name__ == "__main__":
    ingest_protocols()
