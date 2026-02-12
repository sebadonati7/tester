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
        # Fasi che richiedono RAG (Lazy RAG - solo durante il triage clinico)
        rag_phases = [
            "FASE_4_TRIAGE",           # Percorso C - Indagine clinica
            "FAST_TRIAGE_A",           # Percorso A - Domande emergenza
            "VALUTAZIONE_RISCHIO_B"    # Percorso B - Valutazione salute mentale
        ]

        # Lazy RAG: usa i protocolli **solo** nelle fasi cliniche esplicite.
        # Niente fallback basato su keyword generiche: evita attivazioni premature
        # durante la fase di accoglienza/intake.
        return phase in rag_phases
    
    def retrieve_context(
        self, 
        query: str, 
        k: int = 5,
        protocol_filter: Optional[str] = None
    ) -> List[Dict]:
        """
        Ricerca full-text sui protocolli clinici.
        
        Args:
            query: Sintomo/domanda da cercare
            k: Numero di chunks da recuperare
            protocol_filter: Filtra per protocollo specifico (es. "salute-mentale")
            
        Returns:
            Lista di chunks rilevanti
        """
        if not self.supabase:
            logger.warning("⚠️ Supabase non disponibile")
            return []
        
        try:
            # Ricerca full-text con funzione PostgreSQL
            response = self.supabase.rpc(
                'search_protocols',
                {
                    'search_query': query,
                    'max_results': k
                }
            ).execute()
            
            if response.data:
                chunks = response.data
                
                # Filtra per protocollo se specificato
                if protocol_filter:
                    chunks = [
                        c for c in chunks 
                        if protocol_filter.lower() in c.get('protocol', '').lower()
                    ]
                
                logger.info(f"🔍 Trovati {len(chunks)} chunks per: '{query[:50]}...'")
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
        """
        Formatta chunks per prompt Groq.
        
        Args:
            chunks: Chunks recuperati
            phase: Fase corrente (per personalizzare istruzioni)
            
        Returns:
            Context string formattato
        """
        if not chunks:
            return self._get_fallback_context(phase)
        
        # Header
        context = "=== PROTOCOLLI CLINICI PERTINENTI ===\n\n"
        context += "IMPORTANTE: Usa SOLO le informazioni seguenti per generare domande.\n\n"
        
        # Chunks
        for i, chunk in enumerate(chunks, 1):
            source = chunk.get('source', 'Unknown')
            page = chunk.get('page', '?')
            content = chunk.get('content', '')
            
            context += f"[FONTE {i}] {source} (pagina {page})\n"
            context += f"{content}\n\n"
            context += "─" * 80 + "\n\n"
        
        # Footer con istruzioni specifiche per fase
        context += "=== FINE PROTOCOLLI ===\n\n"
        context += self._get_phase_instructions(phase)
        
        return context
    
    def _get_fallback_context(self, phase: str) -> str:
        """Context di fallback quando RAG non trova nulla."""
        if phase == "FAST_TRIAGE_A":
            return (
                "⚠️ ATTENZIONE: Nessun protocollo specifico trovato.\n"
                "Procedi con domande generiche di fast-triage per emergenze:\n"
                "- Quando è iniziato il sintomo?\n"
                "- Il dolore si irradia?\n"
                "- Ci sono difficoltà respiratorie?\n"
            )
        elif phase == "VALUTAZIONE_RISCHIO_B":
            return (
                "⚠️ ATTENZIONE: Nessun protocollo specifico trovato.\n"
                "Procedi con valutazione rischio salute mentale:\n"
                "- Presenza di pensieri autolesivi?\n"
                "- Supporto sociale disponibile?\n"
                "- Storia di trattamenti precedenti?\n"
            )
        else:
            return (
                "⚠️ ATTENZIONE: Nessun protocollo specifico trovato.\n"
                "Procedi con domande generali di triage medico.\n"
            )
    
    def _get_phase_instructions(self, phase: str) -> str:
        """Istruzioni specifiche per fase."""
        instructions = {
            "FASE_4_TRIAGE": """
**ISTRUZIONI FASE 4 - TRIAGE STANDARD (Percorso C):**

Genera UNA SOLA domanda diagnostica basata sui protocolli sopra.

**Formato obbligatorio:**
Domanda + 3 opzioni (A, B, C)

**Esempio:**
"Per capire meglio la situazione, ho bisogno di sapere: il dolore è costante o intermittente?

A) È costante, non si ferma mai
B) Va e viene a ondate
C) È presente solo in alcuni movimenti"

**Range domande:** 5-7 domande totali per codice Green/Yellow.
Se emergono nuovi sintomi gravi → passa a Percorso A.

Genera ora la domanda più pertinente.
""",
            "FAST_TRIAGE_A": """
**ISTRUZIONI FAST-TRIAGE (Percorso A - EMERGENZA):**

Genera domande rapide per valutare gravità (3-4 domande totali).

**Formato:** Domande chiuse (SI/NO) o scala numerica.

**Esempio:**
"Il dolore è iniziato improvvisamente o gradualmente?

• IMPROVVISO (come un fulmine)
• GRADUALE (è aumentato piano)"

**Obiettivo:** Confermare/escludere Codice Rosso/Arancione.
Sii diretto, professionale, veloce.

Genera ora la domanda più critica.
""",
            "VALUTAZIONE_RISCHIO_B": """
**ISTRUZIONI VALUTAZIONE RISCHIO (Percorso B - SALUTE MENTALE):**

Genera domande delicate per valutare rischio.

**Formato:** Domande aperte o chiuse, tono empatico.

**Esempio:**
"Per capire come supportarti al meglio, vorrei chiederti: in questo momento hai pensieri che ti spaventano o ti preoccupano?

• SÌ, vorrei parlarne
• NO, ma mi sento sopraffatto
• Preferisco non rispondere"

**Obiettivo:** Identificare urgenza (118 + hotline) o servizio territoriale (CSM).

Genera ora una domanda sensibile ma necessaria.
"""
        }
        
        return instructions.get(phase, "Genera una domanda clinica appropriata.")
    
    def get_stats(self) -> Dict:
        """Statistiche database."""
        if not self.supabase:
            return {"error": "Non connesso", "chunks": 0}
        
        try:
            response = self.supabase.table("protocol_chunks").select("id", count="exact").execute()
            return {
                "total_chunks": response.count,
                "backend": "Supabase Full-Text Search",
                "embedding_model": "Nessuno (ricerca testuale)"
            }
        except Exception as e:
            return {"error": str(e), "chunks": 0}


# Singleton
@st.cache_resource
def get_rag_service() -> RAGService:
    """Get cached RAG service instance."""
    return RAGService()
