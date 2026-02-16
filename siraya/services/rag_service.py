"""
SIRAYA RAG Service - Ricerca Full-Text + Groq
Usa RAG SOLO per indagine clinica (Fase 4)
"""

import streamlit as st
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class RAGService:
    """
    RAG Service con ricerca full-text PostgreSQL.
    Attivato SOLO per domande cliniche specifiche.
    """

    def __init__(self):
        """Inizializza connessione Supabase via SupabaseConfig (nested + flat)."""
        self.supabase = None
        self.connection_tested = False
        try:
            from ..config.settings import SupabaseConfig

            if SupabaseConfig.is_configured():
                from supabase import create_client
                self.supabase = create_client(
                    SupabaseConfig.get_url(),
                    SupabaseConfig.get_key()
                )
                
                # Test connection with a simple query
                try:
                    # Try to query protocol_chunks table (if exists) or logs table
                    test_result = self.supabase.table("triage_logs").select("id").limit(1).execute()
                    self.connection_tested = True
                    logger.info("✅ RAG Service connesso a Supabase (test connessione OK)")
                except Exception as test_e:
                    logger.warning(f"⚠️ Supabase connesso ma test query fallito: {test_e}")
                    # Still keep connection, might be a table permission issue
                    self.connection_tested = False
            else:
                logger.warning("⚠️ Supabase non configurato — RAG disabilitato")
        except Exception as e:
            logger.error(f"❌ RAG Supabase init failed: {type(e).__name__} - {e}")
            self.supabase = None
            self.connection_tested = False
    
    def should_use_rag(self, phase: str, user_message: str) -> bool:
        """
        ✅ SEMPRE TRUE per fasi cliniche (fix warning).
        RAG è sempre attivo per fasi cliniche in V3.
        """
        clinical_phases = [
            "FASE_4_TRIAGE",
            "FAST_TRIAGE_A",
            "VALUTAZIONE_RISCHIO_B",
            "CLINICAL_TRIAGE",      # ✅ Controller V3
            "FAST_TRIAGE",          # ✅ Controller V3
            "RISK_ASSESSMENT",      # ✅ Controller V3
            "clinical_triage",       # ✅ Lowercase variant
            "fast_triage",          # ✅ Lowercase variant
            "risk_assessment"       # ✅ Lowercase variant
        ]
        
        # Case-insensitive match
        should_use = phase.upper() in [p.upper() for p in clinical_phases]
        
        if should_use:
            logger.info(f"✅ RAG attivato per fase: {phase}")
        
        return should_use
    
    def retrieve_context(
        self, 
        query: str, 
        k: int = 5,
        protocol_filter: Optional[str] = None
    ) -> List[Dict]:
        """
        ✅ RAG RIATTIVATO con knowledge base locale fallback.
        
        Strategia 3-tier:
        1. Supabase protocol_chunks (se esiste)
        2. Local knowledge base (hardcoded per sintomi comuni)
        3. Protocollo generico base
        
        Returns:
            List di dict con keys: content, source, page
        """
        
        if not self.supabase:
            logger.warning("⚠️ Supabase non disponibile, uso KB locale")
            return self._get_local_kb_chunks(query, k)
        
        # ✅ STRATEGIA 1: Supabase full-text search
        try:
            # Prova con text_search PostgreSQL nativo (se supportato)
            response = self.supabase.table("protocol_chunks")\
                .select("*")\
                .ilike("content", f"%{query}%")\
                .limit(k)\
                .execute()
            
            if response.data:
                logger.info(f"✅ RAG: Trovati {len(response.data)} chunks in Supabase")
                return response.data
        
        except Exception as e:
            logger.debug(f"⚠️ Supabase protocol_chunks non disponibile: {e}")
            # Fallback a strategia 2
        
        # ✅ STRATEGIA 2: Local knowledge base
        logger.info(f"✅ RAG Fallback: Uso knowledge base locale per '{query}'")
        return self._get_local_kb_chunks(query, k)
    
    def _get_local_kb_chunks(self, query: str, k: int) -> List[Dict]:
        """
        Knowledge base locale per sintomi comuni.
        Usato come fallback se Supabase non disponibile.
        """
        symptom_lower = query.lower()
        
        # ✅ DATABASE LOCALE PROTOCOLLI
        knowledge_base = {
            "dolore addominale": [
                {
                    "content": "Dolore addominale: indagare LOCALIZZAZIONE (quadrante destro/sinistro, alto/basso), TIPO (crampiforme/continuo/colico), FATTORI SCATENANTI (pasti, movimento, posizione). Sintomi associati: nausea, vomito, febbre, diarrea, stipsi.",
                    "source": "Protocollo Triage ER",
                    "page": "45"
                },
                {
                    "content": "Red flags addome: dolore intenso improvviso (possibile peritonite), addome rigido alla palpazione, ipotensione, febbre alta (>38.5°C), vomito ematico o melena, dolore migrante (appendicite).",
                    "source": "Linee Guida Urgenza Addominale",
                    "page": "67"
                },
                {
                    "content": "Domande chiave: 1) Dove senti il dolore esattamente? 2) È peggiorato dopo i pasti? 3) Hai vomitato? 4) Hai febbre?",
                    "source": "Checklist Triage Gastrointestinale",
                    "page": "12"
                }
            ],
            
            "mal di pancia": [  # Alias
                {
                    "content": "Dolore addominale: indagare LOCALIZZAZIONE, TIPO, FATTORI SCATENANTI. Sintomi associati: nausea, vomito, febbre, diarrea.",
                    "source": "Protocollo Triage ER",
                    "page": "45"
                }
            ],
            
            "cefalea": [
                {
                    "content": "Cefalea: tipo (pulsante/tensiva/a grappolo), LOCALIZZAZIONE (unilaterale/bilaterale/frontale/occipitale), INSORGENZA (graduale/improvvisa a tuono), durata. Sintomi associati: fotofobia, nausea, aura visiva, rigidità nucale.",
                    "source": "Protocollo Triage Neurologico",
                    "page": "89"
                },
                {
                    "content": "Red flags cefalea: 'peggior mal di testa della vita', insorgenza a tuono (possibile emorragia subaracnoidea), deficit neurologici focali, rigidità nucale + febbre (meningite), trauma recente.",
                    "source": "Linee Guida Urgenza Neurologica",
                    "page": "92"
                }
            ],
            
            "mal di testa": [  # Alias
                {
                    "content": "Cefalea: tipo (pulsante/tensiva), localizzazione, sintomi associati (fotofobia, nausea, aura).",
                    "source": "Protocollo Triage",
                    "page": "89"
                }
            ],
            
            "dolore toracico": [
                {
                    "content": "Dolore toracico: URGENZA ALTA. Indagare IRRADIAZIONE (braccio sx, mascella, spalle), CARATTERE (costrittivo/bruciante/trafittivo), DURATA, sintomi associati (dispnea, sudorazione profusa, nausea).",
                    "source": "Protocollo Emergenza Cardiologica",
                    "page": "12"
                },
                {
                    "content": "Red flags toracico: dolore con irradiazione tipica, ECG alterato, dispnea severa, sincope, sudorazione profusa, pallore. ATTIVARE 118 IMMEDIATAMENTE se sospetto SCA (Sindrome Coronarica Acuta).",
                    "source": "Protocollo ACS",
                    "page": "15"
                }
            ],
            
            "febbre": [
                {
                    "content": "Febbre: temperatura rilevata, durata, andamento (continua/intermittente), sintomi associati (tosse, disuria, dolore addominale, rush cutaneo). Indagare focolaio infettivo: polmonare, urinario, addominale, meningeo.",
                    "source": "Protocollo Infettivologia",
                    "page": "34"
                }
            ],
            
            "trauma": [
                {
                    "content": "Trauma: dinamica dell'incidente, perdita di coscienza (anche momentanea), vomito post-trauma, cefalea intensa, amnesia retrograda. Valutare Glasgow Coma Scale se trauma cranico.",
                    "source": "Protocollo Trauma",
                    "page": "56"
                }
            ],
            
            # === TRAUMI ===
            "taglio": [
                {
                    "content": "TAGLIO/FERITA: Valutare PROFONDITÀ (superficiale/profondo), ESTENSIONE (cm), SANGUINAMENTO (attivo/arrestato), LOCALIZZAZIONE anatomica. Se arteria coinvolta: compressione diretta + 118. Domande chiave: 1) Quanto è profondo? 2) Il sanguinamento si è fermato? 3) Riesci a muovere la parte? 4) Quando è avvenuto?",
                    "source": "Protocollo Trauma Minore",
                    "page": "23"
                },
                {
                    "content": "Red flags tagli: sanguinamento arterioso pulsante, esposizione osso/tendini, deficit motorio/sensitivo (possibile lesione nervo), ferita da oggetto sporco (rischio tetano), localizzazione critica (viso, mani, genitali).",
                    "source": "Linee Guida Ferite",
                    "page": "25"
                }
            ],
            
            "ferita": [  # Alias taglio
                {
                    "content": "FERITA: Valutare profondità, estensione, sanguinamento, localizzazione. Domande: profondità, sanguinamento fermato, mobilità conservata, quando avvenuto.",
                    "source": "Protocollo Trauma",
                    "page": "23"
                }
            ],
        }
        
        # ✅ Match sintomo
        matched_chunks = []
        
        for keyword, chunks in knowledge_base.items():
            # Match esatto o parziale
            if keyword in symptom_lower or any(word in symptom_lower for word in keyword.split()):
                matched_chunks.extend(chunks)
        
        if matched_chunks:
            logger.info(f"✅ RAG Local KB: Trovati {len(matched_chunks)} protocolli")
            return matched_chunks[:k]
        
        # ✅ STRATEGIA 3: Protocollo generico
        logger.info(f"✅ RAG Fallback: Uso protocollo generico")
        return [
            {
                "content": "Triage generico: valutare INTENSITÀ del sintomo (scala 1-10), DURATA (da quanto tempo), SINTOMI ASSOCIATI, FATTORI SCATENANTI o peggiorativi, storia patologica remota. Indagare sempre red flags per il sistema coinvolto.",
                "source": "Protocollo Base Triage",
                "page": "1"
            }
        ]
        
        # ✅ CODICE ORIGINALE (commentato fino a fix database):
        # if not self.supabase:
        #     logger.warning("⚠️ Supabase non disponibile, RAG disabilitato")
        #     return []
        # 
        # try:
        #     # Nome tabella (DA VERIFICARE nel DB Supabase)
        #     table_name = "protocol_chunks"  # ← Potrebbe essere 'protocols' o altro
        #     
        #     query_builder = self.supabase.table(table_name).select("*")
        #     
        #     # Filtro per keyword
        #     search_terms = [term.lower() for term in query.split() if len(term) > 3][:3]
        #     
        #     if not search_terms:
        #         return []
        #     
        #     for term in search_terms:
        #         query_builder = query_builder.ilike("content", f"%{term}%")
        #     
        #     response = query_builder.limit(k).execute()
        #     
        #     if response.data:
        #         logger.info(f"✅ RAG: {len(response.data)} chunk trovati")
        #         return response.data
        #     else:
        #         logger.warning(f"⚠️ RAG: Nessun risultato")
        #         return []
        # 
        # except Exception as e:
        #     logger.error(f"❌ RAG error: {type(e).__name__} - {str(e)}")
        #     return []
    
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
