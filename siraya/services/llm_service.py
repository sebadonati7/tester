import re
import json
import logging
import asyncio
import difflib
import os
from typing import Dict, List, Any, Optional, AsyncGenerator, Set
from concurrent.futures import ThreadPoolExecutor
import streamlit as st
from groq import Groq
import google.generativeai as genai

from ..config.settings import EMERGENCY_RULES, ClinicalMappings, RAGConfig

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SymptomNormalizer:
    """Normalizza i sintomi descritti dall'utente usando fuzzy matching."""
    
    def __init__(self):
        self.canonical_kb = ClinicalMappings.CANONICAL_KB
        self.stop_words = ClinicalMappings.STOP_WORDS
        logger.info("SymptomNormalizer initialized")
    
    def _preprocess(self, text: str) -> str:
        """Preprocessa il testo rimuovendo stop words e normalizzando."""
        text = text.lower().strip()
        words = text.split()
        filtered_words = [w for w in words if w not in self.stop_words]
        return " ".join(filtered_words)
    
    def normalize(self, user_symptom: str, threshold: float = 0.6) -> str:
        """
        Normalizza il sintomo usando fuzzy matching contro la knowledge base.
        
        Args:
            user_symptom: Sintomo descritto dall'utente
            threshold: Soglia di similarità (0-1)
            
        Returns:
            Sintomo normalizzato o quello originale se nessun match
        """
        preprocessed = self._preprocess(user_symptom)
        
        best_match = None
        best_score = 0.0
        
        for canonical_term in self.canonical_kb.keys():
            # Confronta con il termine canonico
            score = difflib.SequenceMatcher(None, preprocessed, canonical_term.lower()).ratio()
            
            if score > best_score:
                best_score = score
                best_match = canonical_term
            
            # Confronta anche con i sinonimi
            if canonical_term in self.canonical_kb:
                for synonym in self.canonical_kb[canonical_term]:
                    syn_score = difflib.SequenceMatcher(None, preprocessed, synonym.lower()).ratio()
                    if syn_score > best_score:
                        best_score = syn_score
                        best_match = canonical_term
        
        if best_score >= threshold and best_match:
            logger.info(f"Normalized '{user_symptom}' to '{best_match}' (score: {best_score:.2f})")
            return best_match
        
        logger.info(f"No normalization for '{user_symptom}' (best score: {best_score:.2f})")
        return user_symptom


class DiagnosisSanitizer:
    """Blocca diagnosi e prescrizioni non autorizzate."""
    
    # Pattern regex per identificare frasi proibite
    FORBIDDEN_PATTERNS = [
        r'\bhai\s+(la|il|un|una)\b.*\b(malattia|patologia|sindrome|infezione|disturbo)\b',
        r'\bsoffri\s+di\b',
        r'\bdiagnosi\s+(di|è)\b',
        r'\bprescrivo\b',
        r'\bterapia\s+(con|di)\b',
        r'\bdevi\s+prendere\b',
        r'\bti\s+consiglio\s+(di\s+prendere|il\s+farmaco)\b',
        r'\bassumi\s+(questo|il)\s+farmaco\b',
        r'\b(è|sei)\s+(sicuramente|probabilmente)\s+un[ao]?\b',
    ]
    
    WARNING_MESSAGE = (
        "\n\n⚠️ **ATTENZIONE**: Questa risposta potrebbe contenere elementi non autorizzati. "
        "SIRAYA non può fornire diagnosi o prescrizioni mediche. "
        "Consulta sempre un professionista sanitario qualificato."
    )
    
    @staticmethod
    def sanitize(response_text: str) -> str:
        """
        Verifica se la risposta contiene diagnosi o prescrizioni proibite.
        
        Args:
            response_text: Testo della risposta da sanitizzare
            
        Returns:
            Testo originale o testo con warning se trovati pattern proibiti
        """
        if not response_text:
            return response_text
        
        text_lower = response_text.lower()
        
        for pattern in DiagnosisSanitizer.FORBIDDEN_PATTERNS:
            if re.search(pattern, text_lower):
                logger.warning(f"Forbidden pattern detected: {pattern}")
                return response_text + DiagnosisSanitizer.WARNING_MESSAGE
        
        return response_text


class LLMService:
    """Servizio principale per gestire le interazioni con LLM (Groq e Gemini)."""
    
    def __init__(self):
        self._groq_client: Optional[Groq] = None
        self._gemini_model = None
        self._executor = ThreadPoolExecutor(max_workers=3)
        self._symptom_normalizer = SymptomNormalizer()
        self._prompts = self._load_prompts()
        
        # Inizializza i client
        self._init_clients()
        
        logger.info("LLMService initialized")
    
    def _init_clients(self) -> None:
        """Inizializza i client Groq e Gemini con gestione secrets."""
        
        # Inizializza Groq
        try:
            groq_api_key = None
            
            # Prova a caricare da Streamlit secrets
            if hasattr(st, 'secrets') and 'groq' in st.secrets:
                groq_api_key = st.secrets["groq"]["api_key"]
                logger.debug("Groq API key loaded from Streamlit secrets")
            else:
                # Fallback a variabile d'ambiente
                groq_api_key = os.getenv("GROQ_API_KEY")
                logger.debug("Groq API key loaded from environment variable")
            
            if groq_api_key:
                self._groq_client = Groq(api_key=groq_api_key)
                logger.info("Groq client initialized successfully")
            else:
                logger.warning("Groq API key not found")
                
        except Exception as e:
            logger.error(f"Failed to initialize Groq client: {e}")
        
        # Inizializza Gemini
        try:
            gemini_api_key = None
            
            # Prova a caricare da Streamlit secrets
            if hasattr(st, 'secrets') and 'gemini' in st.secrets:
                gemini_api_key = st.secrets["gemini"]["api_key"]
                logger.debug("Gemini API key loaded from Streamlit secrets")
            else:
                # Fallback a variabile d'ambiente
                gemini_api_key = os.getenv("GEMINI_API_KEY")
                logger.debug("Gemini API key loaded from environment variable")
            
            if gemini_api_key:
                genai.configure(api_key=gemini_api_key)
                self._gemini_model = genai.GenerativeModel('gemini-pro')
                logger.info("Gemini client initialized successfully")
            else:
                logger.warning("Gemini API key not found")
                
        except Exception as e:
            logger.error(f"Failed to initialize Gemini client: {e}")
    
    def _build_system_prompt_with_rag(self, user_symptoms: str, context: Dict[str, Any]) -> str:
        """
        Costruisce il prompt di sistema includendo contesto RAG se necessario.
        
        Args:
            user_symptoms: Sintomi descritti dall'utente
            context: Contesto conversazionale e dati paziente
            
        Returns:
            System prompt completo
        """
        from ..services.rag_service import get_rag_service
        
        phase = context.get("phase", "CHIEF_COMPLAINT")
        rag = get_rag_service()
        
        # Determina se usare RAG
        should_use_rag = rag.should_use_rag(phase, user_symptoms)
        
        rag_context = ""
        if should_use_rag:
            logger.info(f"Using RAG for phase: {phase}")
            
            # Recupera protocolli rilevanti
            retrieved_docs = rag.retrieve_context(user_symptoms, top_k=3)
            
            if retrieved_docs:
                rag_context = rag.format_context_for_llm(retrieved_docs)
                logger.debug(f"RAG context retrieved: {len(retrieved_docs)} documents")
        
        # Estrai dati paziente dal context
        collected_data = context.get("collected_data", {})
        patient_age = collected_data.get("age", "N/A")
        patient_sex = collected_data.get("sex", "N/A")
        patient_location = collected_data.get("location", "N/A")
        percorso = context.get("percorso", "C")
        
        # Costruisci il prompt base con regole
        base_rules = self._prompts.get("base_rules", "")
        
        # Costruisci sezione contesto paziente
        patient_context = f"""
## DATI PAZIENTE
- Età: {patient_age}
- Sesso: {patient_sex}
- Località: {patient_location}
- Percorso: {percorso}
"""
        
        # Costruisci sezione codici colore triage
        triage_rules = """
## CODICI COLORE TRIAGE
- **ROSSO**: Emergenza immediata, rischio vita (es. arresto cardiaco, emorragia massiva)
- **GIALLO**: Urgenza elevata, condizioni instabili (es. dolore toracico, difficoltà respiratoria)
- **VERDE**: Urgenza minore, condizioni stabili (es. traumi lievi, febbre controllata)
- **BIANCO**: Non urgente, può attendere (es. problemi cronici, consulti di routine)
- **ARANCIONE**: Urgenza media-alta (tra giallo e rosso)
- **NERO**: Crisi salute mentale (percorso B)
"""
        
        # Costruisci sezione regole comportamentali
        behavioral_rules = """
## REGOLE COMPORTAMENTALI
- NON fornire diagnosi definitive
- NON prescrivere farmaci o terapie
- Citare sempre le fonti (protocolli clinici) quando disponibili
- Usare un tono professionale ma empatico
- Fare UNA domanda alla volta
- Guidare verso disposizione appropriata (PS, MMG, 118)
"""
        
        # Costruisci sezione output richiesto
        output_format = """
## OUTPUT RICHIESTO
Rispondi in formato JSON:
{
  "text": "Tua risposta al paziente",
  "follow_up_question": "Domanda di approfondimento (se necessaria)",
  "urgency_level": "GREEN|YELLOW|ORANGE|RED|BLACK",
  "sources": ["fonte1", "fonte2"]
}
"""
        
        # Assembla il prompt completo
        system_prompt = f"""{base_rules}

{patient_context}

{triage_rules}

{behavioral_rules}

{rag_context}

{output_format}
"""
        
        # Aggiungi prompt specifico per percorso
        percorso_prompt = self._prompts.get(f"percorso_{percorso.lower()}", "")
        if percorso_prompt:
            system_prompt += f"\n\n## ISTRUZIONI PERCORSO {percorso}\n{percorso_prompt}"
        
        return system_prompt
    
    def _load_prompts(self) -> Dict[str, str]:
        """Carica i template dei prompt."""
        return {
            "base_rules": """Sei SIRAYA, un assistente AI per il triage medico telefonico.
Il tuo compito è raccogliere informazioni sui sintomi del paziente e guidarlo verso la disposizione appropriata.

IMPORTANTE:
- Fai UNA sola domanda alla volta
- NON fornire diagnosi mediche
- NON prescrivere farmaci o terapie
- Usa linguaggio chiaro e professionale
- Mostra empatia e rassicurazione""",
            
            "percorso_a": """PERCORSO A - EMERGENZA
Gestisci situazioni ad alta urgenza (RED/ORANGE).
Priorità: stabilizzare, raccogliere info critiche, indirizzare a 118 o PS immediato.
Sintomi target: dolore toracico, difficoltà respiratoria severa, perdita coscienza, emorragie.""",
            
            "percorso_b": """PERCORSO B - SALUTE MENTALE
Gestisci crisi psichiatriche e disagio mentale.
Priorità: valutare rischio autolesionismo, fornire numeri utili (118, 1522, Telefono Amico).
Tono: empatico, non giudicante, rassicurante.""",
            
            "percorso_c": """PERCORSO C - STANDARD
Gestisci situazioni a urgenza minore (GREEN/YELLOW).
Priorità: raccolta completa anamnesi, indirizzare a MMG o PS non urgente.
Sintomi target: febbre, dolori lievi-moderati, problemi cronici.""",
            
            "disposition_prompt": """Genera un report SBAR (Situation-Background-Assessment-Recommendation) per il personale sanitario.
Include:
- Situazione: sintomo principale e urgenza
- Background: età, sesso, storia clinica rilevante
- Assessment: valutazione codice colore e rischi
- Recommendation: disposizione consigliata (PS, MMG, 118, attesa)"""
        }
    
    def is_available(self) -> bool:
        """Verifica se almeno un LLM è disponibile."""
        available = self._groq_client is not None or self._gemini_model is not None
        logger.debug(f"LLM availability: {available}")
        return available
    
    def check_emergency(self, message: str) -> Optional[Dict]:
        """
        Verifica se il messaggio contiene keyword di emergenza.
        
        Args:
            message: Messaggio dell'utente
            
        Returns:
            Dict con info emergenza se trovata, None altrimenti
        """
        message_lower = message.lower()
        
        # Controlla red flags critiche
        for keyword in EMERGENCY_RULES.CRITICAL_RED_FLAGS:
            if keyword.lower() in message_lower:
                logger.warning(f"CRITICAL RED FLAG detected: {keyword}")
                return {
                    "text": self.get_emergency_response(keyword),
                    "urgency": "RED",
                    "type": "critical",
                    "call_118": True
                }
        
        # Controlla crisi salute mentale
        for keyword in EMERGENCY_RULES.MENTAL_HEALTH_CRISIS:
            if keyword.lower() in message_lower:
                logger.warning(f"MENTAL HEALTH CRISIS detected: {keyword}")
                return {
                    "text": self._get_mental_health_crisis_response(),
                    "urgency": "BLACK",
                    "type": "mental_health",
                    "call_118": True
                }
        
        return None
    
    def get_emergency_response(self, symptom: str) -> str:
        """Genera un messaggio preformattato per emergenza."""
        return f"""🚨 **EMERGENZA RILEVATA** 🚨

Hai segnalato: **{symptom}**

Questo è un sintomo che richiede intervento immediato.

**CHIAMA SUBITO IL 118**

Mentre aspetti i soccorsi:
- Resta calmo e in un luogo sicuro
- Non muoverti se hai traumi
- Se possibile, fatti assistere da qualcuno
- Tieni il telefono a portata di mano

Non spegnere questa app, potremmo chiederti ulteriori informazioni."""
    
    def _get_mental_health_crisis_response(self) -> str:
        """Genera messaggio per crisi salute mentale."""
        return """🆘 **SUPPORTO IMMEDIATO DISPONIBILE**

Capisco che stai attraversando un momento difficile. Non sei solo/a.

**NUMERI UTILI IMMEDIATI:**
- **118** - Emergenza sanitaria (disponibile 24/7)
- **1522** - Antiviolenza e stalking (disponibile 24/7)
- **Telefono Amico** - 02 2327 2327 (tutti i giorni 10-24)
- **Telefono Azzurro** - 19696 (per minori, 24/7)

Se hai pensieri di farti del male o hai bisogno di supporto immediato, contatta uno di questi numeri.

Vuoi che continui a raccogliere informazioni per indirizzarti verso il supporto più appropriato?"""
    
    def get_fallback_response(self, phase: str) -> str:
        """Genera domande di fallback per ogni fase."""
        fallback_questions = {
            "LOCATION": "Mi puoi dire in che città o zona ti trovi?",
            "CHIEF_COMPLAINT": "Qual è il problema principale che ti porta a contattarci oggi?",
            "ONSET": "Da quanto tempo hai questo sintomo?",
            "PAIN_SCALE": "Su una scala da 1 a 10, quanto è intenso il dolore o fastidio?",
            "DEMOGRAPHICS": "Quanti anni hai?",
            "SEX": "Sei maschio o femmina? (Questa informazione aiuta la valutazione medica)",
            "HISTORY": "Hai altre condizioni mediche o stai prendendo farmaci?",
            "ALLERGIES": "Hai allergie note a farmaci o altre sostanze?",
        }
        
        return fallback_questions.get(phase, "Puoi fornirmi maggiori dettagli sulla tua situazione?")
    
    def get_ai_response(self, user_input: str, context: Dict[str, Any]) -> str:
        """
        Metodo principale per ottenere risposta dall'LLM.
        
        Args:
            user_input: Input dell'utente
            context: Contesto conversazionale
            
        Returns:
            Risposta testuale dall'LLM
        """
        try:
            # Normalizza sintomi se presenti
            normalized_input = self._symptom_normalizer.normalize(user_input)
            
            # Costruisci system prompt con RAG
            system_prompt = self._build_system_prompt_with_rag(normalized_input, context)
            
            # Costruisci contesto conversazionale
            context_section = self._build_context_section(context.get("collected_data", {}))
            
            user_message = f"{context_section}\n\nUtente: {normalized_input}"
            
            # Prova prima con Groq
            if self._groq_client:
                try:
                    logger.info("Attempting Groq API call...")
                    response = self._groq_client.chat.completions.create(
                        model="llama-3.3-70b-versatile",
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_message}
                        ],
                        temperature=0.7,
                        max_tokens=1024,
                    )
                    
                    response_text = response.choices[0].message.content
                    logger.info("Groq API call successful")
                    
                    # Sanitizza la risposta
                    sanitized_response = DiagnosisSanitizer.sanitize(response_text)
                    return sanitized_response
                    
                except Exception as e:
                    logger.error(f"Groq API failed: {e}")
            
            # Fallback a Gemini
            if self._gemini_model:
                try:
                    logger.info("Attempting Gemini API call (fallback)...")
                    full_prompt = f"{system_prompt}\n\n{user_message}"
                    
                    response = self._gemini_model.generate_content(full_prompt)
                    response_text = response.text
                    logger.info("Gemini API call successful")
                    
                    # Sanitizza la risposta
                    sanitized_response = DiagnosisSanitizer.sanitize(response_text)
                    return sanitized_response
                    
                except Exception as e:
                    logger.error(f"Gemini API failed: {e}")
            
            # Se tutti i metodi falliscono
            logger.error("All LLM methods failed")
            phase = context.get("phase", "CHIEF_COMPLAINT")
            return self.get_fallback_response(phase)
            
        except Exception as e:
            logger.error(f"Error in get_ai_response: {e}", exc_info=True)
            return "Mi dispiace, si è verificato un errore. Puoi ripetere per favore?"
    
    def _build_context_section(self, collected_data: Dict) -> str:
        """
        Formatta i dati raccolti in una sezione di contesto leggibile.
        
        Args:
            collected_data: Dati raccolti durante la conversazione
            
        Returns:
            Stringa formattata con il contesto
        """
        context_parts = ["=== DATI RACCOLTI ==="]
        
        if "location" in collected_data:
            context_parts.append(f"Località: {collected_data['location']}")
        
        if "chief_complaint" in collected_data:
            context_parts.append(f"Sintomo principale: {collected_data['chief_complaint']}")
        
        if "pain_scale" in collected_data:
            context_parts.append(f"Scala dolore: {collected_data['pain_scale']}/10")
        
        if "age" in collected_data:
            context_parts.append(f"Età: {collected_data['age']}")
        
        if "sex" in collected_data:
            context_parts.append(f"Sesso: {collected_data['sex']}")
        
        if "onset" in collected_data:
            context_parts.append(f"Insorgenza: {collected_data['onset']}")
        
        if "history" in collected_data:
            context_parts.append(f"Anamnesi: {collected_data['history']}")
        
        context_parts.append("=" * 20)
        
        return "\n".join(context_parts)


# Singleton pattern
_llm_service: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    """
    Restituisce l'istanza singleton di LLMService.
    
    Returns:
        Istanza di LLMService
    """
    global _llm_service
    
    if _llm_service is None:
        logger.info("Creating new LLMService instance")
        _llm_service = LLMService()
    
    return _llm_service
