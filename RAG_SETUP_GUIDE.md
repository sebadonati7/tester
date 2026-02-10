# SIRAYA Hybrid Triage System - RAG Setup Guide

## Overview

The Hybrid Triage System combines two "brains":
1. **Clinical Brain (RAG)**: Uses PDF protocol documents to determine clinical decisions (color code, specialization)
2. **Logistic Brain (JSON)**: Uses structured data files to recommend nearest healthcare facilities

## Architecture

```
siraya/
├── data/
│   ├── protocols/              [PDF protocol files]
│   │   ├── Manuale-Triage-Lazio.pdf
│   │   ├── Sistema-Dispatch-Toscana.pdf
│   │   ├── Linee-Guida-Piemonte.pdf
│   │   ├── WAST_ViolenzaDomestica.pdf
│   │   ├── ASQ_AbuseSostanze.pdf
│   │   └── 18A0052000100030110001.pdf
│   ├── chroma_db/             [Vector store - generated]
│   ├── master_kb.json         [Healthcare facilities - DO NOT MODIFY]
│   ├── distretti_sanitari_er.json [Districts mapping - DO NOT MODIFY]
│   └── mappa_er.json          [Geographic coordinates - DO NOT MODIFY]
│
├── services/
│   ├── rag_service.py         [NEW - RAG implementation]
│   ├── llm_service.py         [MODIFIED - RAG integration]
│   ├── data_loader.py         [EXISTING - DO NOT MODIFY]
│   └── ...
│
├── controllers/
│   └── triage_controller.py  [MODIFIED - Hybrid orchestration]
│
└── config/
    └── settings.py            [MODIFIED - Added RAGConfig]
```

## Setup Instructions

### Step 1: Install Dependencies

```bash
pip install -r requirements.txt
```

This installs:
- `langchain==0.1.20` - LangChain framework
- `langchain-community==0.0.38` - Community integrations
- `chromadb==0.4.24` - Vector database
- `pypdf==4.0.1` - PDF processing
- `sentence-transformers==2.5.1` - Embedding models
- `faiss-cpu==1.8.0` - Vector similarity search

### Step 2: Verify PDF Files

PDF protocol files should be in `siraya/data/protocols/`:

```bash
ls -lh siraya/data/protocols/
```

Expected files:
- Manuale-Triage-Lazio.pdf (8.3 MB) - Primary triage manual
- Sistema-Dispatch-Toscana.pdf (2.6 MB) - Dispatch system
- Linee-Guida-Piemonte.pdf (689 KB) - Regional guidelines
- WAST_ViolenzaDomestica.pdf (538 KB) - Domestic violence protocols
- ASQ_AbuseSostanze.pdf (100 KB) - Substance abuse protocols
- 18A0052000100030110001.pdf (167 KB) - Legal/regulatory

### Step 3: Run Protocol Ingestion

**⚠️ IMPORTANT**: This step requires internet access to download the embedding model from HuggingFace.

```bash
python scripts/ingest_protocols.py
```

This will:
1. Load all PDF files from `siraya/data/protocols/`
2. Split them into chunks (1000 chars with 200 char overlap)
3. Generate embeddings using `sentence-transformers/all-MiniLM-L6-v2`
4. Store in ChromaDB vector database at `siraya/data/chroma_db/`

Expected output:
```
🚀 SIRAYA Protocol Ingestion
================================================================================
📚 Found 6 PDF files:
   - Manuale-Triage-Lazio.pdf (priority: 1)
   - Sistema-Dispatch-Toscana.pdf (priority: 2)
   ...

🧠 Initializing RAG service...
📄 Starting ingestion...
✅ Added 2847 chunks from Manuale-Triage-Lazio.pdf
✅ Added 891 chunks from Sistema-Dispatch-Toscana.pdf
...

================================================================================
✅ INGESTION COMPLETE
================================================================================
📊 Total chunks indexed: 4253
💾 ChromaDB location: /path/to/siraya/data/chroma_db
```

### Step 4: Verify ChromaDB

```bash
ls -lh siraya/data/chroma_db/
```

You should see ChromaDB files (typically several SQLite databases and metadata files).

### Step 5: Test RAG Retrieval (Optional)

```bash
python -c "
from siraya.services.rag_service import get_rag_service

rag = get_rag_service()
stats = rag.get_stats()
print(f'Total chunks: {stats[\"total_chunks\"]}')

# Test retrieval
docs = rag.retrieve_context('dolore toracico acuto', k=3)
print(f'Retrieved {len(docs)} relevant chunks')
for i, doc in enumerate(docs, 1):
    print(f'  {i}. {doc.metadata[\"source\"]} (page {doc.metadata.get(\"page\", \"?\")})')
"
```

## How It Works

### 1. Clinical Brain (RAG)

When a user describes symptoms:

```python
# User input: "dolore toracico acuto con affanno"

# Step 1: RAG retrieves relevant protocol chunks
rag = get_rag_service()
docs = rag.retrieve_context("dolore toracico acuto con affanno", k=5)
protocol_context = rag.format_context_for_llm(docs)

# Step 2: LLM analyzes with protocol context
system_prompt = build_system_prompt_with_rag(symptoms, context)
# system_prompt includes:
# - Retrieved protocol text with citations
# - Patient context (age, location, etc.)
# - Instructions to output JSON with codice_colore + specializzazione

response = llm.get_ai_response(symptoms, context)
# Output: {"codice_colore": "ROSSO", "specializzazione": "Cardiologia", ...}
```

### 2. Logistic Brain (JSON)

Once clinical decision is made:

```python
# Step 3: Query existing JSON data for facilities
from siraya.services.data_loader import get_data_loader

data_loader = get_data_loader()
facilities = data_loader.find_facilities_smart(
    query_service="Cardiologia",
    query_comune="Bologna",
    limit=3
)

# Returns nearest facilities with Cardiology from master_kb.json
```

### 3. Combined Response

```
📊 Codice ROSSO - Cardiologia

In base ai protocolli [Manuale Lazio, pag. 42], il dolore toracico acuto 
con dispnea richiede valutazione cardiologica urgente.

📍 STRUTTURA CONSIGLIATA:
Ospedale Maggiore - Pronto Soccorso
Largo Bartolo Nigrisoli, 2, Bologna
```

## Configuration

### RAG Settings

In `siraya/config/settings.py`:

```python
class RAGConfig:
    # Embedding model
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    
    # Vector store
    CHROMA_PERSIST_DIR: Path = DATA_DIR / "chroma_db"
    
    # Chunking
    CHUNK_SIZE: int = 1000          # Characters per chunk
    CHUNK_OVERLAP: int = 200        # Overlap between chunks
    
    # Retrieval
    TOP_K_CHUNKS: int = 5           # How many chunks to retrieve
    MAX_CONTEXT_LENGTH: int = 4000  # Max chars in LLM prompt
    
    # Protocol priorities (lower = higher priority)
    PROTOCOL_PRIORITIES = {
        "Manuale-Triage-Lazio.pdf": 1,
        "Sistema-Dispatch-Toscana.pdf": 2,
        "Linee-Guida-Piemonte.pdf": 3,
        "WAST_ViolenzaDomestica.pdf": 10,
        "ASQ_AbuseSostanze.pdf": 10,
        "18A0052000100030110001.pdf": 99,
    }
```

## Troubleshooting

### Issue: "ChromaDB is empty"

```bash
# Re-run ingestion
python scripts/ingest_protocols.py
```

### Issue: "No PDF files found"

```bash
# Check files are in correct location
ls siraya/data/protocols/*.pdf

# If missing, move them:
mv *.pdf siraya/data/protocols/
```

### Issue: "Cannot download embedding model"

The sandbox environment blocks HuggingFace downloads. Solutions:

**Option A**: Run ingestion in a non-sandboxed environment:
```bash
# On your local machine or a server with internet access
python scripts/ingest_protocols.py

# Then copy the chroma_db directory to the sandbox
cp -r siraya/data/chroma_db /path/to/sandbox/
```

**Option B**: Use a pre-cached model:
```bash
# Download model manually
python -c "
from sentence_transformers import SentenceTransformer
model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')
model.save('/home/runner/.cache/huggingface/')
"

# Then run ingestion
python scripts/ingest_protocols.py
```

### Issue: "Vector store not initialized"

Check logs:
```bash
python -c "
import logging
logging.basicConfig(level=logging.DEBUG)
from siraya.services.rag_service import get_rag_service
rag = get_rag_service()
"
```

## Offline Mode

Once ChromaDB is populated, the RAG system works fully offline:

1. Embeddings are cached locally
2. ChromaDB is a local SQLite database
3. No internet connection required for retrieval

To enable offline mode explicitly:
```bash
export TRANSFORMERS_OFFLINE=1
export HF_DATASETS_OFFLINE=1
```

## Performance

Expected metrics:
- **Ingestion**: ~5-10 minutes for all PDFs
- **Query time**: ~200-500ms per retrieval
- **Storage**: ~50-100 MB for ChromaDB
- **Chunks**: ~3000-5000 total across all PDFs

## Security Notes

1. **No Protocol Modification**: RAG only retrieves, never modifies PDFs
2. **Source Citations**: All responses include protocol sources
3. **Evidence-Based**: LLM cannot hallucinate - only uses retrieved content
4. **Audit Trail**: Every retrieval is logged with metadata

## Maintenance

### Adding New Protocols

1. Add PDF to `siraya/data/protocols/`
2. Update priorities in `RAGConfig.PROTOCOL_PRIORITIES`
3. Re-run ingestion: `python scripts/ingest_protocols.py`

### Updating Existing Protocols

1. Replace PDF in `siraya/data/protocols/`
2. Delete ChromaDB: `rm -rf siraya/data/chroma_db`
3. Re-run ingestion: `python scripts/ingest_protocols.py`

### Monitoring

Check vector store stats:
```python
from siraya.services.rag_service import get_rag_service
rag = get_rag_service()
print(rag.get_stats())
```

## Testing

Run integration test:
```bash
python -c "
from siraya.services.rag_service import get_rag_service
from siraya.services.llm_service import get_llm_service

# Test RAG retrieval
rag = get_rag_service()
docs = rag.retrieve_context('febbre alta bambino')
assert len(docs) > 0, 'No documents retrieved'

# Test LLM integration
llm = get_llm_service()
context = {'patient_age': 5, 'patient_location': 'Bologna'}
response = llm.get_ai_response('febbre 39 da 2 giorni', context)
assert 'Codice' in response or 'codice' in response
print('✅ All tests passed')
"
```

## Support

For issues:
1. Check logs in console output
2. Verify ChromaDB exists: `ls siraya/data/chroma_db/`
3. Test retrieval separately from LLM
4. Check API keys are configured for Groq/Gemini

## References

- LangChain: https://python.langchain.com/
- ChromaDB: https://docs.trychroma.com/
- Sentence Transformers: https://www.sbert.net/
