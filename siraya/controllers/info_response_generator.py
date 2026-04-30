"""
InfoResponseGenerator — Genera risposte informative libere, conversazionali, non SBAR.

Differenze da triage:
- Nessun SBAR
- Tono amichevole, NON medico-formale
- Risposta adattata al tipo di domanda (intent)
- Facility/opzioni solo se rilevanti
- Mantiene contesto conversazionale
- MAI "NON HO TROVATO RISULTATI" — sempre domande di raffinamento o alternative
"""

import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


# Intent categories
class InfoIntent:
    OPERATING_HOURS = "OPERATING_HOURS"
    FACILITY_LOCATION = "FACILITY_LOCATION"
    SERVICE_INFO = "SERVICE_INFO"
    COST_INFO = "COST_INFO"
    PROCEDURE_INFO = "PROCEDURE_INFO"
    GENERAL_HEALTH = "GENERAL_HEALTH"
    PRESCRIPTION_INFO = "PRESCRIPTION_INFO"
    OTHER = "OTHER"


class InfoResponseGenerator:
    """
    Genera risposte informative libere, conversazionali, non standardizzate SBAR.

    Flow:
    1. Estrai intent semantico da user_query (LLM-based con fallback keyword)
    2. RAG retrieval con query semantica + intent + location
    3. Se rag_results non vuoti: LLM genera risposta adattata all'intent
    4. Se rag_results vuoti: domande di raffinamento o alternative costruttive
    """

    # Intent detection keywords (fallback se LLM non disponibile)
    INTENT_KEYWORDS = {
        InfoIntent.OPERATING_HOURS: [
            "orari", "orario", "aperto", "chiuso", "apertura", "chiusura",
            "quando apre", "quando chiude", "a che ora", "fino a che ora",
        ],
        InfoIntent.FACILITY_LOCATION: [
            "dove si trova", "dove è", "indirizzo", "dov'è", "come arrivo",
            "come raggiungo", "come si arriva", "dove sono", "struttura",
        ],
        InfoIntent.COST_INFO: [
            "costo", "quanto costa", "prezzo", "ticket", "pagamento",
            "gratuito", "gratis", "tariffa", "esenzione",
        ],
        InfoIntent.PROCEDURE_INFO: [
            "come si prenota", "prenotazione", "come accedo", "procedura",
            "come funziona", "come si fa", "documenti", "cosa serve",
            "passaggi", "steps",
        ],
        InfoIntent.PRESCRIPTION_INFO: [
            "ricetta", "prescrizione", "farmaco", "medicinale", "farmacia",
            "ricetta medica", "impegnativa",
        ],
        InfoIntent.GENERAL_HEALTH: [
            "sintomi", "malattia", "diagnosi", "cura", "trattamento",
            "medicina", "salute", "patologia",
        ],
    }

    # Intent-specific LLM instructions
    INTENT_INSTRUCTIONS = {
        InfoIntent.OPERATING_HOURS: (
            "Fornisci gli orari in modo CONCISO e diretto. "
            "Se ci sono variazioni (es. festivi, stagionali), indicale. "
            "Formato: giorni/orari chiari. Max 3-4 righe."
        ),
        InfoIntent.FACILITY_LOCATION: (
            "Fornisci l'indirizzo preciso e indica come raggiungerlo. "
            "Sii pratico: mezzi pubblici, parcheggio, punti di riferimento. "
            "Max 4-5 righe."
        ),
        InfoIntent.SERVICE_INFO: (
            "Descrivi cosa offre il servizio, chi può accedervi e come procedere. "
            "Struttura: Cosa fa | Chi può accedere | Come si accede. Max 4-5 righe."
        ),
        InfoIntent.COST_INFO: (
            "Fornisci informazioni sui costi in modo chiaro. "
            "Indica eventuali esenzioni, agevolazioni, o come sapere se si ha diritto. "
            "Max 3-4 righe."
        ),
        InfoIntent.PROCEDURE_INFO: (
            "Spiega la procedura in modo step-by-step. "
            "Indica documenti necessari, tempi, dove andare. "
            "Usa elenco puntato se ci sono più passaggi. Max 5-6 righe."
        ),
        InfoIntent.GENERAL_HEALTH: (
            "Fornisci informazioni accurate sulla salute. "
            "Aggiungi un disclaimer discreto che per diagnosi/cure serve un medico. "
            "Max 4-5 righe."
        ),
        InfoIntent.PRESCRIPTION_INFO: (
            "Spiega come funziona la ricetta/prescrizione in modo pratico. "
            "Indica passi, documenti, tempi. Max 4-5 righe."
        ),
        InfoIntent.OTHER: (
            "Rispondi in modo amichevole e utile alla domanda. "
            "Se non hai informazioni sufficienti, proponi alternative concrete. "
            "Max 4-5 righe."
        ),
    }

    def __init__(self, rag_service, llm_service):
        """
        Args:
            rag_service: RAGService instance con metodo retrieve_context_for_info()
            llm_service: LLMService instance
        """
        self.rag_service = rag_service
        self.llm_service = llm_service

    def generate_info_response(
        self,
        user_query: str,
        location: Optional[str] = None,
        conversation_history: Optional[List[Dict]] = None,
    ) -> str:
        """
        Genera risposta informativa conversazionale, libera da SBAR.

        Flow:
        1. Estrai intent semantico
        2. RAG retrieval con filtri
        3. Se risultati: risposta LLM adattata all'intent
        4. Se vuoti: raffinamento/alternative, MAI fallback generico

        Returns:
            Stringa di risposta in italiano, tono amichevole
        """
        if not conversation_history:
            conversation_history = []

        # Step 1: Estrai intent
        intent = self.extract_query_intent(user_query)
        logger.info(f"🔍 InfoResponseGenerator: intent='{intent}' per '{user_query[:60]}'")

        # Step 2: RAG retrieval
        rag_results = self._retrieve_rag_context(user_query, intent, location)
        logger.info(f"📚 InfoResponseGenerator: {len(rag_results)} risultati RAG")

        # Step 3: Genera risposta
        if rag_results:
            response = self._generate_from_rag_results(
                user_query=user_query,
                rag_results=rag_results,
                intent=intent,
                conversation_history=conversation_history,
            )
        else:
            response = self._generate_refinement_or_alternative(
                user_query=user_query,
                intent=intent,
                location=location,
            )

        return response

    def extract_query_intent(self, user_query: str) -> str:
        """
        Estrae intent semantico dalla query.

        Usa keyword matching come fallback affidabile.

        Returns:
            Una delle costanti InfoIntent
        """
        query_lower = user_query.lower()

        # Keyword matching (deterministico, robusto)
        for intent, keywords in self.INTENT_KEYWORDS.items():
            if any(kw in query_lower for kw in keywords):
                return intent

        # Se LLM disponibile, prova a classificare semanticamente
        if self.llm_service and self.llm_service.is_available():
            try:
                prompt = f"""Classifica questa domanda in UNA delle seguenti categorie.
Rispondi SOLO con il nome della categoria, nessun testo aggiuntivo.

Domanda: "{user_query}"

Categorie:
- OPERATING_HOURS (orari, quando apre/chiude)
- FACILITY_LOCATION (dove si trova, indirizzo, come arrivare)
- SERVICE_INFO (cosa offre, chi accede, come funziona)
- COST_INFO (costi, ticket, esenzioni)
- PROCEDURE_INFO (come prenotare, documenti, passaggi)
- GENERAL_HEALTH (sintomi, malattie, cure)
- PRESCRIPTION_INFO (ricette, farmaci, prescrizioni)
- OTHER (altro)

Categoria:"""
                result = self.llm_service.generate_with_json_parse(
                    prompt + '\n{"intent": "CATEGORIA_QUI"}',
                    temperature=0.0,
                    max_tokens=50,
                )
                if result and result.get("intent"):
                    detected_intent = result["intent"].strip().upper()
                    valid_intents = [
                        InfoIntent.OPERATING_HOURS, InfoIntent.FACILITY_LOCATION,
                        InfoIntent.SERVICE_INFO, InfoIntent.COST_INFO,
                        InfoIntent.PROCEDURE_INFO, InfoIntent.GENERAL_HEALTH,
                        InfoIntent.PRESCRIPTION_INFO, InfoIntent.OTHER,
                    ]
                    if detected_intent in valid_intents:
                        return detected_intent
            except Exception as e:
                logger.debug(f"⚠️ LLM intent detection failed: {e}")

        return InfoIntent.OTHER

    def _retrieve_rag_context(
        self,
        user_query: str,
        intent: str,
        location: Optional[str],
    ) -> List[Dict]:
        """
        Recupera contesto RAG con filtri per intent e location.
        Usa retrieve_context_for_info() se disponibile, altrimenti retrieve_context().
        """
        try:
            # Usa il metodo specializzato per INFO se disponibile
            if hasattr(self.rag_service, "retrieve_context_for_info"):
                results = self.rag_service.retrieve_context_for_info(
                    query=user_query,
                    intent=intent,
                    location=location,
                    top_k=5,
                )
                return results

            # Fallback al metodo standard
            results = self.rag_service.retrieve_context(user_query, k=5)
            return results if results else []

        except Exception as e:
            logger.error(f"❌ InfoResponseGenerator: RAG retrieval failed: {e}")
            return []

    def _generate_from_rag_results(
        self,
        user_query: str,
        rag_results: List[Dict],
        intent: str,
        conversation_history: Optional[List[Dict]] = None,
    ) -> str:
        """
        Genera risposta da RAG results con LLM e prompt dinamico per intent.
        """
        if not conversation_history:
            conversation_history = []

        # Formato contesto RAG
        context_text = "\n\n".join([
            f"[Fonte: {r.get('source', 'DB')}]\n{r.get('content', '')}"
            for r in rag_results[:4]
        ])

        # Istruzioni specifiche per intent
        intent_instruction = self.INTENT_INSTRUCTIONS.get(
            intent, self.INTENT_INSTRUCTIONS[InfoIntent.OTHER]
        )

        # Storia conversazione recente
        history_text = ""
        if conversation_history:
            recent = conversation_history[-4:]
            lines = [
                f"{'Utente' if m.get('role') == 'user' else 'Assistente'}: {m.get('content', '')[:150]}"
                for m in recent
            ]
            history_text = "\nConversazione precedente:\n" + "\n".join(lines)

        prompt = f"""Sei un assistente sanitario amichevole. Rispondi alla domanda dell'utente in modo conversazionale.

DOMANDA UTENTE: "{user_query}"
{f"CITTÀ/ZONA: {location}" if "location" else ""}

INFORMAZIONI DISPONIBILI:
{context_text}
{history_text}

ISTRUZIONI PER QUESTA RISPOSTA:
{intent_instruction}

REGOLE:
- Tono amichevole e accessibile, NON medico-formale
- NON usare formato SBAR
- Rispondi direttamente alla domanda
- Se l'informazione non è nelle fonti, dillo onestamente e proponi alternative
- In italiano
- Rispondi in testo libero (NO JSON)"""

        try:
            # Per info response, usiamo il client LLM direttamente (risposta in testo libero)
            if self.llm_service._groq_client:
                response = self.llm_service._groq_client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.4,
                    max_tokens=400,
                )
                return response.choices[0].message.content.strip()
            elif self.llm_service._gemini_model:
                response = self.llm_service._gemini_model.generate_content(prompt)
                return response.text.strip()
        except Exception as e:
            logger.error(f"❌ InfoResponseGenerator: LLM failed: {e}")

        # Fallback testuale dai risultati RAG
        return self._format_rag_fallback(rag_results, intent)

    def _generate_refinement_or_alternative(
        self,
        user_query: str,
        intent: str,
        location: Optional[str],
    ) -> str:
        """
        Quando RAG è vuoto: genera domande di raffinamento o alternative.
        MAI "NON HO TROVATO RISULTATI".
        """
        query_lower = user_query.lower()

        # Genera risposta contestuale basata sull'intent
        if intent == InfoIntent.OPERATING_HOURS:
            if not location:
                return (
                    "Per trovare gli orari giusti, potresti dirmi in quale città "
                    "o distretto ti trovi? Così posso darti le informazioni specifiche per la struttura vicino a te."
                )
            return (
                f"Non ho trovato gli orari specifici per {location}. "
                f"Puoi contattare direttamente il CUP (Centro Unico Prenotazioni) "
                f"al numero verde 800 033 033 per informazioni aggiornate."
            )

        if intent == InfoIntent.FACILITY_LOCATION:
            if not location:
                return (
                    "Per trovare la struttura giusta vicino a te, puoi dirmi "
                    "in quale città o comune dell'Emilia-Romagna ti trovi?"
                )
            return (
                f"Per trovare strutture sanitarie a {location}, puoi consultare "
                f"il portale AUSL locale o chiamare il CUP al numero verde 800 033 033."
            )

        if intent == InfoIntent.COST_INFO:
            return (
                "I costi dipendono dalla tua situazione specifica (esenzioni, tipo di prestazione). "
                "Per informazioni precise, contatta il CUP al numero verde 800 033 033 "
                "o visita il sito della tua AUSL di riferimento."
            )

        if intent == InfoIntent.PROCEDURE_INFO:
            return (
                "Per la procedura specifica, ti consiglio di contattare direttamente "
                "la struttura o il CUP al numero verde 800 033 033. "
                "Puoi anche dirmi di quale servizio hai bisogno in modo più specifico?"
            )

        # Fallback generico ma costruttivo
        return (
            "Non ho informazioni specifiche su questa richiesta nel mio database. "
            "Puoi dirmi di più su cosa stai cercando? Ad esempio: "
            "il nome del servizio, la città, o il tipo di prestazione che ti interessa."
        )

    def _format_rag_fallback(self, rag_results: List[Dict], intent: str) -> str:
        """Formatta i risultati RAG come testo semplice se LLM non disponibile."""
        if not rag_results:
            return "Puoi fornirmi più dettagli sulla tua richiesta per aiutarti meglio?"

        parts = []
        for r in rag_results[:2]:
            content = r.get("content", "").strip()
            if content:
                parts.append(content[:300])

        if parts:
            return "\n\n".join(parts)

        return "Puoi fornirmi più dettagli sulla tua richiesta per aiutarti meglio?"
