"""
InfoProcessor — Elaborazione query INFO con LLM + RAG.

Estrae la location dalla query, recupera documenti rilevanti via RAG,
e genera una risposta conversazionale tramite LLM.
"""

import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class InfoProcessor:
    """Processa query INFO con LLM + RAG semantico."""

    def __init__(self, rag_service, llm_service):
        """
        Args:
            rag_service: Istanza di RAGService (per retrieve_info_documents).
            llm_service: Istanza di LLMService (per generate_text).
        """
        self.rag_service = rag_service
        self.llm_service = llm_service

    def process_info_query(
        self,
        user_query: str,
        location: Optional[str],
        conversation_history: List[Dict],
    ) -> Dict:
        """
        Elabora query INFO: RAG retrieval + risposta LLM.

        Args:
            user_query: Domanda dell'utente.
            location: Comune già noto (da turni precedenti) o None.
            conversation_history: Lista di dict {"role": "user"|"assistant", "content": str}.

        Returns:
            Dict con chiavi "response" (str) e "docs_used" (int).
        """
        # Step 1: Estrai location se non fornita
        if not location:
            location = self._extract_location(user_query)
            logger.debug(f"InfoProcessor: location estratta='{location}'")

        if not location:
            return {
                "response": "In quale città cerchi questa informazione? 📍",
                "docs_used": 0,
            }

        # Step 2: RAG retrieval
        docs = self.rag_service.retrieve_info_documents(
            query=user_query,
            location=location,
            top_k=3,
        )

        logger.info(
            f"InfoProcessor: RAG → {len(docs)} documenti per '{user_query[:50]}' in {location}"
        )

        if not docs:
            return {
                "response": (
                    f"Non trovo dettagli specifici a {location}. "
                    "Che servizio cerchi esattamente? (es: CAU, Pronto Soccorso, consultorio...)"
                ),
                "docs_used": 0,
            }

        # Step 3: LLM genera risposta conversazionale
        prompt = self._build_prompt(user_query, docs, conversation_history, location)
        response = self.llm_service.generate_text(prompt=prompt)

        if not response:
            # Fallback testuale dai documenti
            response = self._text_fallback(docs, location)

        logger.debug(f"InfoProcessor: risposta LLM generata ({len(response)} chars)")

        return {
            "response": response,
            "docs_used": len(docs),
        }

    def _extract_location(self, query: str) -> Optional[str]:
        """
        Estrae location da query naturale.
        Es: "orari del CAU di Ravenna" → "Ravenna"
            "numero consultorio a San Giovanni in Persiceto" → "San Giovanni in Persiceto"
        """
        # Pattern: preposizione seguita da nome città (supporta nomi composti)
        patterns = [
            r"(?:di|a|presso|c/o)\s+([A-Z][a-zà-ù]+(?:\s+(?:di\s+)?[A-Za-zà-ù]+)*)",
            r"(?:a|in)\s+([A-Z][a-zà-ù]+(?:\s+(?:di\s+)?[A-Za-zà-ù]+)*)\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, query)
            if match:
                # Pulisci stopwords finali che potrebbero essere aggiunte per errore
                location = match.group(1).strip()
                # Rimuovi parole finali che sono preposizioni/articoli italiani
                stopwords = {"di", "del", "della", "dei", "delle", "il", "la", "i", "le", "gli"}
                parts = location.split()
                while parts and parts[-1].lower() in stopwords:
                    parts.pop()
                if parts:
                    return " ".join(parts)
        return None

    def _build_prompt(
        self,
        query: str,
        docs: List[Dict],
        history: List[Dict],
        location: str,
    ) -> str:
        """Costruisce prompt conversazionale per LLM."""
        docs_text = "\n---\n".join(
            f"DOCUMENTO: {doc.get('title', '')}\n{doc.get('content', '')}"
            for doc in docs
        )

        history_text = ""
        for turn in history[-4:]:
            role = "Utente" if turn.get("role") == "user" else "Tu"
            history_text += f"{role}: {turn.get('content', '')}\n"

        prompt = f"""Sei SIRAYA, un assistente sanitario. Rispondi alla domanda usando SOLO i documenti forniti.

DOMANDA UTENTE: "{query}"
LOCATION: {location}

CONVERSAZIONE PRECEDENTE:
{history_text if history_text else "(Prima domanda)"}

DOCUMENTI DISPONIBILI:
{docs_text}

ISTRUZIONI:
1. Rispondi direttamente con le informazioni dai documenti (orari, contatti, indirizzo se richiesti)
2. Mantieni tono professionale ma cordiale
3. Se la domanda riguarda orari/contatti, includili in modo chiaro
4. Se opportuno, aggiungi UNA domanda di follow-up per aiutare ulteriormente
5. MAI inventare informazioni non presenti nei documenti
6. Se i documenti non contengono la risposta esatta, dichiaralo onestamente

RISPOSTA:"""
        return prompt

    def _text_fallback(self, docs: List[Dict], location: str) -> str:
        """Fallback testuale quando LLM non è disponibile."""
        lines = [f"Ecco le informazioni per {location}:\n"]
        for doc in docs:
            title = doc.get("title", "Struttura")
            content = doc.get("content", "")
            lines.append(f"📍 **{title}**\n{content[:300]}")
        lines.append("\nPosso aiutarti con altro?")
        return "\n\n".join(lines)
