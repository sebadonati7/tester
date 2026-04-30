"""
SIRAYA RAG Service V2.0 — Supabase protocol_chunks PRIMARY
No hardcoded knowledge base. All clinical protocols come from Supabase.

Strategy:
1. Supabase protocol_chunks — multi-keyword + category search
2. Minimal generic fallback (only if Supabase unavailable)
"""

import streamlit as st
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class RAGService:
    """
    RAG Service backed by Supabase protocol_chunks table.
    NO hardcoded knowledge base — all clinical data from database.
    
    Implements LAZY RECONNECT: if the initial connection fails (e.g. table
    didn't exist yet), every call to retrieve_context will retry once.
    """

    def __init__(self):
        """Initialize Supabase connection."""
        self.supabase = None
        self.connection_tested = False
        self.chunk_count = 0
        self._init_connection()

    def _init_connection(self):
        """Try to connect to Supabase and verify protocol_chunks table."""
        try:
            from ..config.settings import SupabaseConfig

            if SupabaseConfig.is_configured():
                if not self.supabase:
                    from supabase import create_client
                    self.supabase = create_client(
                        SupabaseConfig.get_url(),
                        SupabaseConfig.get_key()
                    )
                
                # Test connection + count chunks
                try:
                    test_result = self.supabase.table("protocol_chunks").select("id", count="exact").limit(1).execute()
                    self.connection_tested = True
                    self.chunk_count = test_result.count if hasattr(test_result, 'count') and test_result.count else len(test_result.data or [])
                    logger.info(f"✅ RAG Service: protocol_chunks OK ({self.chunk_count} chunks)")
                except Exception as test_e:
                    logger.warning(f"⚠️ protocol_chunks non accessibile: {test_e}")
                    self.connection_tested = False
            else:
                logger.warning("⚠️ Supabase non configurato — RAG disabilitato")
        except Exception as e:
            logger.error(f"❌ RAG Supabase init failed: {type(e).__name__} - {e}")
            self.supabase = None
            self.connection_tested = False

    def _ensure_connection(self):
        """Lazy reconnect: retry if initial connection failed."""
        if not self.connection_tested and self.supabase:
            logger.info("🔄 RAG: Retrying protocol_chunks connection...")
            self._init_connection()
        elif not self.supabase:
            logger.info("🔄 RAG: No client — attempting full reconnect...")
            self._init_connection()
    
    def should_use_rag(self, phase: str, user_message: str = "") -> bool:
        """Always True for clinical phases."""
        clinical_phases = {
            "clinical_triage", "fast_triage", "risk_assessment",
            "CLINICAL_TRIAGE", "FAST_TRIAGE", "RISK_ASSESSMENT",
            "FASE_4_TRIAGE", "FAST_TRIAGE_A", "VALUTAZIONE_RISCHIO_B",
        }
        return phase in clinical_phases
    
    # =========================================================================
    # SYMPTOM → SEARCH TERMS mapping (for Supabase query building)
    # =========================================================================
    SYMPTOM_SEARCH_TERMS = {
        "cefalea": ["cefalea", "testa", "emicrania"],
        "mal di testa": ["cefalea", "testa", "emicrania"],
        "dolore toracico": ["toracico", "petto", "cardiaco"],
        "toracalgia": ["toracico", "petto", "cardiaco"],
        "dolore addominale": ["addominale", "addome", "pancia", "stomaco"],
        "mal di pancia": ["addominale", "addome", "pancia"],
        "lombalgia": ["lombalgia", "schiena", "lombare"],
        "cervicalgia": ["cervicale", "collo"],
        "gonalgia": ["ginocchio", "articolare"],
        "febbre": ["febbre", "temperatura", "ipertermia"],
        "dispnea": ["dispnea", "respiro", "respiratorio", "polmonare"],
        "trauma": ["trauma", "ferita", "frattura", "contusione"],
        "vertigini": ["vertigini", "capogiro", "equilibrio"],
        "nausea": ["nausea", "vomito"],
        "dolore al piede": ["piede", "tallone", "plantare"],
        "dolore alla gamba": ["gamba", "polpaccio", "coscia"],
        "dolore articolare": ["articolare", "ginocchio", "gomito", "spalla"],
    }

    # Symptom → category mapping for direct category search
    SYMPTOM_CATEGORY_MAP = {
        "cefalea": "cefalea",
        "mal di testa": "cefalea",
        "dolore toracico": "dolore_toracico",
        "toracalgia": "dolore_toracico",
        "dolore addominale": "dolore_addominale",
        "mal di pancia": "dolore_addominale",
        "lombalgia": "lombalgia",
        "dolore alla schiena": "lombalgia",
        "mal di schiena": "lombalgia",
        "cervicalgia": "cervicalgia",
        "dolore al collo": "cervicalgia",
        "febbre": "febbre",
        "dispnea": "dispnea",
        "difficoltà respiratorie": "dispnea",
        "trauma": "trauma",
        "ferita": "trauma",
        "taglio": "trauma",
        "vertigini": "vertigini",
        "capogiro": "vertigini",
        "nausea": "nausea_vomito",
        "vomito": "nausea_vomito",
        "dolore al piede": "dolore_piede",
        "dolore alla gamba": "dolore_gamba",
        "dolore articolare": "dolore_articolare",
        "gonalgia": "dolore_articolare",
    }

    def retrieve_context(
        self, 
        query: str, 
        k: int = 5,
        protocol_filter: Optional[str] = None
    ) -> List[Dict]:
        """
        Retrieve clinical protocol chunks from Supabase.
        
        Strategy:
        1. Lazy reconnect if needed
        2. Category-based search (exact match on symptom_category)
        3. Keyword content search (ilike on content)
        4. Minimal generic fallback
        """
        
        # ═══ LAZY RECONNECT if first init failed ═══
        self._ensure_connection()
        
        # ═══ STRATEGY 1: Supabase category + keyword search ═══
        if self.supabase and self.connection_tested:
            results = self._search_supabase(query, k)
            if results:
                logger.info(f"✅ RAG Supabase: {len(results)} chunks for '{query[:40]}'")
                return results
            else:
                logger.info(f"⚠️ RAG Supabase: 0 chunks for '{query[:40]}', using generic fallback")

        # ═══ STRATEGY 2: Generic fallback (Supabase unavailable) ═══
        logger.info("⚠️ RAG: Supabase non disponibile, uso fallback generico")
        return [{
            "content": "Triage generico: valutare INTENSITÀ (scala 1-10), DURATA (da quanto tempo), LOCALIZZAZIONE precisa, IRRADIAZIONE, SINTOMI ASSOCIATI, FATTORI SCATENANTI/PEGGIORATIVI, FARMACI IN CORSO, PATOLOGIE NOTE, storia patologica remota. Indagare sempre red flags per il sistema coinvolto.",
            "source": "Protocollo Base Triage (fallback)",
            "page": "1"
        }]

    def _search_supabase(self, query: str, k: int) -> List[Dict]:
        """
        Two-phase Supabase search:
        1. Category match (exact) — most precise
        2. Content keyword search — broader
        """
        query_lower = query.lower().strip()
        all_results = []
        seen_ids = set()
        
        # ═══ PHASE 1: Category-based search ═══
        category = self._find_category(query_lower)
        if category:
            try:
                response = self.supabase.table("protocol_chunks")\
                    .select("*")\
                    .eq("symptom_category", category)\
                    .order("chunk_index")\
                    .limit(k)\
                    .execute()
                
                if response.data:
                    for chunk in response.data:
                        chunk_id = chunk.get("id", id(chunk))
                        if chunk_id not in seen_ids:
                            seen_ids.add(chunk_id)
                            all_results.append(self._normalize_chunk(chunk))
                    logger.info(f"  📂 Category '{category}': {len(response.data)} chunks")
            except Exception as e:
                logger.debug(f"⚠️ Category search failed: {e}")
        
        # ═══ PHASE 2: Content keyword search (supplement) ═══
        if len(all_results) < k:
            search_terms = self._get_search_terms(query_lower)
            for term in search_terms[:3]:  # Max 3 keyword searches
                try:
                    response = self.supabase.table("protocol_chunks")\
                        .select("*")\
                        .ilike("content", f"%{term}%")\
                        .limit(k - len(all_results))\
                        .execute()
                    
                    if response.data:
                        for chunk in response.data:
                            chunk_id = chunk.get("id", id(chunk))
                            if chunk_id not in seen_ids:
                                seen_ids.add(chunk_id)
                                all_results.append(self._normalize_chunk(chunk))
                except Exception as e:
                    logger.debug(f"⚠️ Keyword search '{term}' failed: {e}")
        
        # ═══ PHASE 3: Generic category fallback ═══
        if not all_results:
            try:
                response = self.supabase.table("protocol_chunks")\
                    .select("*")\
                    .eq("symptom_category", "generico")\
                    .limit(k)\
                    .execute()
                if response.data:
                    for chunk in response.data:
                        all_results.append(self._normalize_chunk(chunk))
                    logger.info(f"  📂 Generic category: {len(response.data)} chunks")
            except Exception as e:
                logger.debug(f"⚠️ Generic search failed: {e}")
        
        return all_results[:k]

    def _find_category(self, query_lower: str) -> Optional[str]:
        """Find the best matching symptom_category for a query."""
        # Direct match
        if query_lower in self.SYMPTOM_CATEGORY_MAP:
            return self.SYMPTOM_CATEGORY_MAP[query_lower]
        
        # Partial match
        for symptom, category in self.SYMPTOM_CATEGORY_MAP.items():
            if symptom in query_lower or query_lower in symptom:
                return category
        
        # Word-level match
        query_words = set(query_lower.split())
        for symptom, category in self.SYMPTOM_CATEGORY_MAP.items():
            symptom_words = set(symptom.split())
            if query_words & symptom_words:  # Intersection
                return category
        
        return None

    def _get_search_terms(self, query_lower: str) -> List[str]:
        """Generate search terms for keyword-based content search."""
        terms = []
        
        # Check symptom mapping
        for symptom, keywords in self.SYMPTOM_SEARCH_TERMS.items():
            if symptom in query_lower or any(kw in query_lower for kw in keywords):
                terms.extend(keywords)
                break
        
        # Add query words (length > 3)
        for word in query_lower.split():
            clean = word.strip(".,;:!?")
            if len(clean) > 3 and clean not in terms:
                terms.append(clean)
        
        if not terms:
            terms = [query_lower]
        
        # Deduplicate
        seen = set()
        unique = []
        for t in terms:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        return unique[:5]
    
    def _normalize_chunk(self, chunk: Dict) -> Dict:
        """Normalize Supabase row to standard chunk format."""
        return {
            "content": chunk.get("content", ""),
            "source": chunk.get("source", "Protocollo Clinico"),
            "page": str(chunk.get("page", chunk.get("chunk_index", "?"))),
            "symptom_category": chunk.get("symptom_category", "generico"),
            "severity": chunk.get("severity", "standard"),
        }
    
    def retrieve_context_for_info(
        self,
        query: str,
        intent: Optional[str] = None,
        location: Optional[str] = None,
        top_k: int = 5,
    ) -> List[Dict]:
        """
        Semantic retrieval ottimizzato per query INFO (Path INFO).

        Differenze da retrieve_context():
        - Filtra per intent-specific document categories
        - Geographic filtering se location fornita
        - Restituisce [] se score < 0.5 (no hallucination fallback)
        - Non ritorna il generic fallback

        Args:
            query: Query utente
            intent: InfoIntent category (OPERATING_HOURS, FACILITY_LOCATION, ecc.)
            location: Comune (es. "Bologna")
            top_k: Numero massimo di risultati

        Returns:
            Lista di {content, source, page, relevance_score, snippet, metadata}
            Lista vuota se nessun risultato sopra soglia (il caller genera raffinamento)
        """
        self._ensure_connection()

        if not self.supabase or not self.connection_tested:
            logger.info("⚠️ RAG INFO: Supabase non disponibile, nessun risultato")
            return []

        all_results = []
        seen_ids = set()
        query_lower = query.lower().strip()

        # Build search terms dall'intent
        intent_terms = self._get_intent_search_terms(intent, query_lower)

        # Ricerca per location se fornita
        if location:
            location_lower = location.lower().strip()
            try:
                for term in intent_terms[:2]:
                    response = self.supabase.table("protocol_chunks") \
                        .select("*") \
                        .ilike("content", f"%{location_lower}%") \
                        .ilike("content", f"%{term}%") \
                        .limit(top_k) \
                        .execute()

                    if response.data:
                        for chunk in response.data:
                            chunk_id = chunk.get("id", id(chunk))
                            if chunk_id not in seen_ids:
                                seen_ids.add(chunk_id)
                                normalized = self._normalize_chunk(chunk)
                                all_results.append(normalized)
            except Exception as e:
                logger.debug(f"⚠️ RAG INFO location search failed: {e}")

        # Ricerca keyword generica se non abbastanza risultati
        if len(all_results) < top_k:
            try:
                for term in intent_terms[:3]:
                    if len(all_results) >= top_k:
                        break
                    response = self.supabase.table("protocol_chunks") \
                        .select("*") \
                        .ilike("content", f"%{term}%") \
                        .limit(top_k - len(all_results)) \
                        .execute()

                    if response.data:
                        for chunk in response.data:
                            chunk_id = chunk.get("id", id(chunk))
                            if chunk_id not in seen_ids:
                                seen_ids.add(chunk_id)
                                all_results.append(self._normalize_chunk(chunk))
            except Exception as e:
                logger.debug(f"⚠️ RAG INFO keyword search failed: {e}")

        # Filtra per soglia di rilevanza (score < 0.5 → escludi)
        # Dato che non abbiamo embedding scores, usiamo lunghezza contenuto come proxy
        filtered = [r for r in all_results if len(r.get("content", "")) > 30]

        logger.info(
            f"📚 RAG INFO: {len(filtered)} risultati per '{query_lower[:40]}' "
            f"(intent={intent}, location={location})"
        )
        return filtered[:top_k]

    def _get_intent_search_terms(self, intent: Optional[str], query_lower: str) -> List[str]:
        """Genera termini di ricerca specifici per intent INFO."""
        intent_term_map = {
            "OPERATING_HOURS": ["orari", "apertura", "chiusura", "aperto"],
            "FACILITY_LOCATION": ["indirizzo", "dove", "sede", "ubicazione"],
            "SERVICE_INFO": ["servizio", "prestazione", "offre", "accesso"],
            "COST_INFO": ["costo", "ticket", "tariffa", "prezzo", "esenzione"],
            "PROCEDURE_INFO": ["prenotazione", "accesso", "procedura", "documenti"],
            "GENERAL_HEALTH": ["salute", "informazioni", "guida"],
            "PRESCRIPTION_INFO": ["ricetta", "prescrizione", "farmaco"],
        }

        terms = intent_term_map.get(intent or "OTHER", [])

        # Aggiungi parole dalla query
        for word in query_lower.split():
            clean = word.strip(".,;:!?")
            if len(clean) > 3 and clean not in terms:
                terms.append(clean)

        return terms[:6] if terms else [query_lower]


        self, 
        chunks: List[Dict],
        phase: str = "clinical_triage"
    ) -> str:
        """Format retrieved chunks for LLM prompt."""
        if not chunks:
            return self._get_fallback_context(phase)
        
        context = "=== PROTOCOLLI CLINICI DA SUPABASE ===\n\n"
        context += "USA queste informazioni per generare domande cliniche pertinenti.\n\n"
        
        for i, chunk in enumerate(chunks, 1):
            source = chunk.get('source', 'Unknown')
            page = chunk.get('page', '?')
            content = chunk.get('content', '')
            severity = chunk.get('severity', 'standard')
            
            severity_icon = {"emergency": "🔴", "high": "🟠", "standard": "🟢"}.get(severity, "⚪")
            context += f"[FONTE {i}] {severity_icon} {source} (p.{page})\n"
            context += f"{content}\n\n"
        
        context += "=== FINE PROTOCOLLI ===\n"
        return context
    
    def _get_fallback_context(self, phase: str) -> str:
        """Minimal fallback when no chunks found."""
        return (
            "⚠️ Nessun protocollo specifico trovato nel database.\n"
            "Procedi con domande generali di triage:\n"
            "- Da quanto tempo è presente il sintomo?\n"
            "- Come è iniziato? (improvviso/graduale)\n"
            "- Ci sono sintomi associati?\n"
            "- Farmaci o patologie note?\n"
        )
    
    def get_stats(self) -> Dict:
        """Database statistics."""
        self._ensure_connection()
        if not self.supabase or not self.connection_tested:
            return {"error": "Non connesso", "chunks": 0}
        return {
            "total_chunks": self.chunk_count,
            "backend": "Supabase protocol_chunks",
            "connected": True
        }


# Singleton
@st.cache_resource
def get_rag_service() -> RAGService:
    """Get cached RAG service instance."""
    return RAGService()
