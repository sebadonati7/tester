"""
SIRAYA RAG Service - Ricerca Full-Text + Groq
Usa RAG SOLO per indagine clinica (Fase 4)
"""

import streamlit as st
from supabase import create_client
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class RAGService:
    """
    RAG Service con ricerca full-text PostgreSQL.
    Attivato SOLO per domande cliniche specifiche.
    """
    
    def __init__(self):
        """Inizializza connessione Supabase."""
        try:
            self.supabase = create_client(
                st.secrets["supabase"]["url"],
                st.secrets["supabase"]["service_role_key"]
            )
            logger.info("✅ RAG Service connesso a Supabase")
        except Exception as e:
            logger.error(f"❌ Errore connessione Supabase: {e}")
            self.supabase = None
    
    def should_use_rag(self, phase: str, user_message: str) -> bool:
        """
        Determina se usare RAG in base alla fase e al messaggio.
        
        RAG attivo SOLO per:
        - Fase 4 (Triage Clinico)
        - Fase 3 (Valutazione Rischio Salute Mentale - Percorso B)
        
        Args:
            phase: Fase corrente (es. "FASE_4_TRIAGE")
            user_message: Messaggio utente
            
        Returns:
            True se RAG necessario
        """
        # Fasi che richiedono RAG
        rag_phases = [
            "FASE_4_TRIAGE",
            "FAST_TRIAGE_A",
            "VALUTAZIONE_RISCHIO_B"
        ]
        
        # Parole chiave cliniche
        clinical_keywords = [
            "dolore", "sintomo", "male", "febbre", "tosse", 
            "nausea", "vomito", "diarrea", "sangue", "gonfiore",
            "respiro", "petto", "testa", "stomaco", "schiena"
        ]
        
        if phase in rag_phases:
            return True
        
        msg_lower = user_message.lower()
        if any(keyword in msg_lower for keyword in clinical_keywords):
            return True
        
        return False
    
    def retrieve_context(
        self, 
        query: str, 
        k: int = 5,
        protocol_filter: Optional[str] = None
    ) -> List[Dict]:
        """Ricerca full-text sui protocolli clinici."""
        if not self.supabase:
            logger.warning("⚠️ Supabase non disponibile")
            return []
        
        try:
            response = self.supabase.rpc(
                'search_protocols',
                {
                    'search_query': query,
                    'max_results': k
                }
            ).execute()
            
            if response.data:
                chunks = response.data
                
                if protocol_filter:
                    chunks = [
                        c for c in chunks 
                        if protocol_filter.lower() in c.get('protocol', '').lower()
                    ]
                
                logger.info(f"🔍 Trovati {len(chunks)} chunks")
                return chunks
            else:
                logger.warning("⚠️ Nessun chunk trovato")
                return []
                
        except Exception as e:
            logger.error(f"❌ Errore ricerca: {e}")
            return []
    
    def format_context_for_llm(
        self, 
        chunks: List[Dict],
        phase: str = "FASE_4_TRIAGE"
    ) -> str:
        """Formatta chunks per prompt Groq."""
        if not chunks:
            return self._get_fallback_context(phase)
        
        context = "=== PROTOCOLLI CLINICI PERTINENTI ===\n\n"
        
        for i, chunk in enumerate(chunks, 1):
            source = chunk.get('source', 'Unknown')
            page = chunk.get('page', '?')
            content = chunk.get('content', '')
            
            context += f"[FONTE {i}] {source} (pagina {page})\n"
            context += f"{content}\n\n"
            context += "─" * 80 + "\n\n"
        
        context += "=== FINE PROTOCOLLI ===\n\n"
        context += self._get_phase_instructions(phase)
        
        return context
    
    def _get_fallback_context(self, phase: str) -> str:
        """Context di fallback."""
        return "⚠️ Nessun protocollo trovato. Procedi con domande generali.\n"
    
    def _get_phase_instructions(self, phase: str) -> str:
        """Istruzioni per fase."""
        instructions = {
            "FASE_4_TRIAGE": """
**ISTRUZIONI TRIAGE STANDARD:**
Genera UNA domanda + 3 opzioni (A, B, C)

Esempio:
"Il dolore è costante o intermittente?

A) Costante
B) A ondate
C) Solo con movimenti"
""",
            "FAST_TRIAGE_A": """
**ISTRUZIONI EMERGENZA:**
Domande rapide SI/NO per valutare gravità.
""",
            "VALUTAZIONE_RISCHIO_B": """
**ISTRUZIONI SALUTE MENTALE:**
Domande delicate con tono empatico.
"""
        }
        
        return instructions.get(phase, "Genera domanda clinica appropriata.")
    
    def get_stats(self) -> Dict:
        """Statistiche database."""
        if not self.supabase:
            return {"error": "Non connesso", "chunks": 0}
        
        try:
            response = self.supabase.table("protocol_chunks").select("id", count="exact").execute()
            return {
                "total_chunks": response.count,
                "backend": "Supabase Full-Text Search"
            }
        except Exception as e:
            return {"error": str(e), "chunks": 0}


@st.cache_resource
def get_rag_service() -> RAGService:
    """Get cached RAG service instance."""
    return RAGService()