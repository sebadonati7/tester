"""
SIRAYA Health Navigator - RAG Service
V1.0: Clinical Brain - Protocol Retrieval

CRITICAL: This service is the ONLY source of clinical decision-making.
Do NOT hallucinate protocols. Use ONLY the indexed PDF content.
"""

import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import streamlit as st
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_core.documents import Document

from ..config.settings import RAGConfig, PATHS

logger = logging.getLogger(__name__)


class RAGService:
    """
    Retrieval-Augmented Generation for clinical protocols.
    
    Workflow:
    1. Ingest PDFs → ChromaDB vector store
    2. User symptom query → Semantic search
    3. Return top-K relevant protocol chunks
    4. Format context for LLM system prompt
    """
    
    def __init__(self):
        """Initialize RAG service with ChromaDB."""
        self.embedding_model = RAGConfig.EMBEDDING_MODEL
        self.persist_dir = RAGConfig.CHROMA_PERSIST_DIR
        
        # Initialize embeddings
        logger.info(f"🧠 Loading embedding model: {self.embedding_model}")
        self.embeddings = HuggingFaceEmbeddings(
            model_name=self.embedding_model,
            model_kwargs={'device': 'cpu'},
            encode_kwargs={'normalize_embeddings': True}
        )
        
        # Load or create vector store
        self.vectorstore = None
        self._init_vectorstore()
    
    def _init_vectorstore(self) -> None:
        """Initialize or load ChromaDB."""
        try:
            if self.persist_dir.exists():
                logger.info(f"📚 Loading ChromaDB from {self.persist_dir}")
                self.vectorstore = Chroma(
                    persist_directory=str(self.persist_dir),
                    embedding_function=self.embeddings
                )
                
                # Verify content
                count = self.vectorstore._collection.count()
                if count == 0:
                    logger.warning("⚠️ ChromaDB is empty. Run ingest_protocols.py")
                else:
                    logger.info(f"✅ ChromaDB loaded: {count} chunks indexed")
            else:
                logger.warning(f"⚠️ ChromaDB not found at {self.persist_dir}")
                logger.warning("⚠️ Creating empty store. Run ingest_protocols.py to populate.")
                self.persist_dir.mkdir(parents=True, exist_ok=True)
                self.vectorstore = Chroma(
                    persist_directory=str(self.persist_dir),
                    embedding_function=self.embeddings
                )
        except Exception as e:
            logger.error(f"❌ ChromaDB initialization error: {e}")
            self.vectorstore = None
    
    # ========================================================================
    # INGESTION (Used by scripts/ingest_protocols.py)
    # ========================================================================
    
    def ingest_pdf(
        self,
        pdf_path: Path,
        chunk_size: int = RAGConfig.CHUNK_SIZE,
        chunk_overlap: int = RAGConfig.CHUNK_OVERLAP
    ) -> int:
        """
        Ingest single PDF into vector store.
        
        Args:
            pdf_path: Path to PDF file
            chunk_size: Text chunk size
            chunk_overlap: Overlap between chunks
            
        Returns:
            Number of chunks created
        """
        try:
            logger.info(f"📄 Ingesting: {pdf_path.name}")
            
            # Load PDF
            loader = PyPDFLoader(str(pdf_path))
            pages = loader.load()
            
            # Add metadata
            for page in pages:
                page.metadata.update({
                    "source": pdf_path.name,
                    "protocol": pdf_path.stem,
                    "priority": RAGConfig.PROTOCOL_PRIORITIES.get(pdf_path.name, 99)
                })
            
            # Split into chunks
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                separators=["\n\n", "\n", ". ", "! ", "? ", "; ", ", ", " "]
            )
            chunks = splitter.split_documents(pages)
            
            # Add to vector store
            if self.vectorstore:
                self.vectorstore.add_documents(chunks)
                logger.info(f"✅ Added {len(chunks)} chunks from {pdf_path.name}")
            else:
                logger.error("❌ Vector store not initialized")
                return 0
            
            return len(chunks)
            
        except Exception as e:
            logger.error(f"❌ Error ingesting {pdf_path.name}: {e}")
            return 0
    
    def ingest_all_protocols(self) -> Dict[str, int]:
        """
        Ingest all PDFs from protocols directory.
        
        Returns:
            Dict mapping filename to chunk count
        """
        protocols_dir = RAGConfig.PROTOCOLS_DIR
        
        if not protocols_dir.exists():
            logger.error(f"❌ Protocols directory not found: {protocols_dir}")
            return {}
        
        pdf_files = sorted(
            protocols_dir.glob("*.pdf"),
            key=lambda p: RAGConfig.PROTOCOL_PRIORITIES.get(p.name, 99)
        )
        
        if not pdf_files:
            logger.error(f"❌ No PDF files found in {protocols_dir}")
            return {}
        
        logger.info(f"📚 Found {len(pdf_files)} PDF files to ingest")
        
        results = {}
        for pdf_path in pdf_files:
            chunk_count = self.ingest_pdf(pdf_path)
            results[pdf_path.name] = chunk_count
        
        # Persist to disk
        if self.vectorstore:
            # Note: persist() is deprecated in ChromaDB 0.4.x but still supported
            # The vector store auto-persists on updates in newer versions
            try:
                self.vectorstore.persist()
                logger.info(f"💾 ChromaDB persisted to {self.persist_dir}")
            except AttributeError:
                # Newer ChromaDB versions auto-persist
                logger.info(f"💾 ChromaDB auto-persisted to {self.persist_dir}")
        
        return results
    
    # ========================================================================
    # RETRIEVAL (Used by llm_service.py)
    # ========================================================================
    
    def retrieve_context(
        self,
        query: str,
        k: int = RAGConfig.TOP_K_CHUNKS
    ) -> List[Document]:
        """
        Retrieve relevant protocol chunks for symptom query.
        
        Args:
            query: User symptom description (e.g., "dolore toracico acuto")
            k: Number of chunks to retrieve
            
        Returns:
            List of relevant Document chunks with metadata
        """
        if not self.vectorstore:
            logger.warning("⚠️ Vector store not initialized")
            return []
        
        try:
            # Semantic search
            results = self.vectorstore.similarity_search(
                query,
                k=k
            )
            
            logger.info(f"🔍 Retrieved {len(results)} chunks for: '{query[:50]}...'")
            
            # Log sources for traceability
            sources = set(doc.metadata.get("source", "Unknown") for doc in results)
            logger.debug(f"📚 Sources: {', '.join(sources)}")
            
            return results
            
        except Exception as e:
            logger.error(f"❌ Retrieval error: {e}")
            return []
    
    def retrieve_with_scores(
        self,
        query: str,
        k: int = RAGConfig.TOP_K_CHUNKS
    ) -> List[Tuple[Document, float]]:
        """
        Retrieve chunks with similarity scores.
        
        Args:
            query: Symptom query
            k: Number of results
            
        Returns:
            List of (document, score) tuples
        """
        if not self.vectorstore:
            return []
        
        try:
            results = self.vectorstore.similarity_search_with_score(query, k=k)
            return results
        except Exception as e:
            logger.error(f"❌ Scored retrieval error: {e}")
            return []
    
    def format_context_for_llm(
        self,
        documents: List[Document],
        max_length: int = RAGConfig.MAX_CONTEXT_LENGTH
    ) -> str:
        """
        Format retrieved chunks into LLM system prompt context.
        
        Args:
            documents: Retrieved chunks
            max_length: Max character length
            
        Returns:
            Formatted context string with source citations
        """
        if not documents:
            return (
                "⚠️ ATTENZIONE: Nessun protocollo specifico trovato per questa query. "
                "Procedi con cautela e applica le linee guida generali di triage."
            )
        
        context_parts = [
            "=== PROTOCOLLI CLINICI PERTINENTI ===\n",
            "IMPORTANTE: Le seguenti informazioni provengono dai manuali ufficiali di triage.\n"
        ]
        
        current_length = len("".join(context_parts))
        
        for i, doc in enumerate(documents, 1):
            source = doc.metadata.get("source", "Unknown")
            page = doc.metadata.get("page", "?")
            priority = doc.metadata.get("priority", 99)
            
            chunk_header = f"\n[FONTE {i}] {source} (pag. {page}, priorità: {priority})\n"
            chunk_text = doc.page_content.strip()
            
            full_chunk = f"{chunk_header}{chunk_text}\n{'─' * 80}\n"
            
            if current_length + len(full_chunk) > max_length:
                break
            
            context_parts.append(full_chunk)
            current_length += len(full_chunk)
        
        context_parts.append(
            "\n=== FINE PROTOCOLLI ===\n"
            "Usa SOLO le informazioni sopra per determinare codice colore e specializzazione.\n"
        )
        
        return "".join(context_parts)
    
    # ========================================================================
    # UTILITIES
    # ========================================================================
    
    def get_stats(self) -> Dict[str, Any]:
        """Get vector store statistics."""
        if not self.vectorstore:
            return {"error": "Vector store not initialized", "chunks": 0}
        
        try:
            count = self.vectorstore._collection.count()
            return {
                "total_chunks": count,
                "embedding_model": self.embedding_model,
                "persist_directory": str(self.persist_dir),
                "protocols_dir": str(RAGConfig.PROTOCOLS_DIR)
            }
        except Exception as e:
            return {"error": str(e), "chunks": 0}


# ============================================================================
# SINGLETON
# ============================================================================

@st.cache_resource
def get_rag_service() -> RAGService:
    """Get cached RAG service instance."""
    return RAGService()
