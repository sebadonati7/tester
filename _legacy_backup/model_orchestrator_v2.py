# model_orchestrator_v2.py
import streamlit as st
import asyncio
import json
import logging
import re
import atexit
import difflib
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, AsyncGenerator, Union, Optional, Set
from pydantic import ValidationError
from datetime import datetime

from models import TriageResponse, TriageMetadata, QuestionType
from smart_router import SmartRouter

logger = logging.getLogger(__name__)


# ============================================================================
# SYMPTOM NORMALIZER (Merged from utils/symptom_normalizer.py)
# ============================================================================

# Canonical symptom names (target medical terms)
CANONICAL_KB: Dict[str, str] = {
    # Cefalea
    "mal di testa": "Cefalea",
    "mal testa": "Cefalea",
    "testa che fa male": "Cefalea",
    "dolore testa": "Cefalea",
    "dolore alla testa": "Cefalea",
    "emicrania": "Cefalea",
    "cefalea": "Cefalea",
    
    # Dolore addominale
    "mal di pancia": "Dolore addominale",
    "mal pancia": "Dolore addominale",
    "dolore pancia": "Dolore addominale",
    "dolore addome": "Dolore addominale",
    "dolore stomaco": "Dolore addominale",
    "mal di stomaco": "Dolore addominale",
    
    # Dolore toracico (RED FLAG)
    "dolore petto": "Dolore toracico",
    "dolore torace": "Dolore toracico",
    "dolore al petto": "Dolore toracico",
    "dolore cuore": "Dolore toracico",
    "oppressione petto": "Dolore toracico",
    "peso sul petto": "Dolore toracico",
    
    # Dispnea
    "difficoltà respirare": "Dispnea",
    "difficolta respiro": "Dispnea",
    "non riesco respirare": "Dispnea grave",
    "non riesco a respirare": "Dispnea grave",
    "soffoco": "Dispnea grave",
    "affanno": "Dispnea",
    "fiato corto": "Dispnea",
    
    # Febbre
    "febbre": "Febbre",
    "temperatura alta": "Febbre",
    "febbrile": "Febbre",
    "ho la febbre": "Febbre",
    
    # Tosse
    "tosse": "Tosse",
    "tossisco": "Tosse",
    "colpi tosse": "Tosse",
    
    # Trauma
    "caduta": "Trauma",
    "sono caduto": "Trauma",
    "sono caduta": "Trauma",
    "botta": "Trauma",
    "incidente": "Trauma",
    "trauma": "Trauma",
    
    # Vertigini
    "vertigini": "Vertigini",
    "capogiro": "Vertigini",
    "giramento testa": "Vertigini",
    "testa che gira": "Vertigini",
    
    # Nausea
    "nausea": "Nausea",
    "voglia vomitare": "Nausea",
    "sto male": "Nausea",
    
    # Vomito
    "vomito": "Vomito",
    "ho vomitato": "Vomito",
    "rimetto": "Vomito",
    
    # Diarrea
    "diarrea": "Diarrea",
    "scariche": "Diarrea",
    "feci liquide": "Diarrea",
    
    # Dolore articolare
    "dolore articolazioni": "Dolore articolare",
    "male alle ossa": "Dolore articolare",
    "dolore ginocchio": "Dolore articolare",
    "dolore schiena": "Lombalgia",
    
    # Mental health
    "ansia": "Ansia",
    "ansioso": "Ansia",
    "ansiosa": "Ansia",
    "attacco panico": "Attacco di panico",
    "panico": "Attacco di panico",
    "depressione": "Depressione",
    "depresso": "Depressione",
    "triste": "Umore depresso",
    "stress": "Stress",
}

# Stop words da rimuovere nel preprocessing
STOP_WORDS: Set[str] = {
    "ho", "hai", "ha", "un", "una", "il", "la", "lo", "di", "da", "in",
    "per", "con", "su", "a", "che", "mi", "ti", "si", "al", "alla",
    "del", "della", "delle", "dei", "degli", "molto", "tanto", "poco"
}


class SymptomNormalizer:
    """
    Normalizes symptom descriptions to canonical medical terms.
    
    Attributes:
        canonical_kb: Dictionary mapping symptom variants to canonical names
        fuzzy_threshold: Minimum similarity for fuzzy matching (0.0-1.0)
        unknown_terms: Set of terms that failed normalization
    """
    
    def __init__(
        self,
        canonical_kb: Optional[Dict[str, str]] = None,
        fuzzy_threshold: float = 0.85
    ):
        """
        Initialize symptom normalizer.
        
        Args:
            canonical_kb: Custom knowledge base (default: built-in)
            fuzzy_threshold: Fuzzy matching threshold (default: 0.85)
        """
        self.canonical_kb = canonical_kb or CANONICAL_KB
        self.fuzzy_threshold = fuzzy_threshold
        self.unknown_terms: Set[str] = set()
        
        logger.info(f"SymptomNormalizer initialized with {len(self.canonical_kb)} entries")
    
    def _preprocess(self, text: str) -> str:
        """
        Preprocess text for normalization.
        
        Steps:
        1. Lowercase
        2. Remove punctuation
        3. Remove stop words
        4. Collapse whitespace
        
        Args:
            text: Raw symptom text
        
        Returns:
            Cleaned text
        """
        if not text:
            return ""
        
        # Lowercase
        text = text.lower().strip()
        
        # Remove punctuation (keep only alphanumeric and spaces)
        text = re.sub(r'[^\w\s]', ' ', text)
        
        # Remove stop words
        words = text.split()
        words = [w for w in words if w not in STOP_WORDS]
        text = ' '.join(words)
        
        # Collapse whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        
        return text
    
    def normalize(
        self,
        symptom: str,
        context: Optional[str] = None
    ) -> str:
        """
        Normalize symptom to canonical medical term.
        
        Algorithm:
        1. Preprocessing (lowercase, remove stop words)
        2. Exact match in canonical_kb
        3. Fuzzy matching with similarity threshold
        4. Context-based disambiguation (if provided)
        5. Fallback to original (marked as Unknown)
        
        Args:
            symptom: User-reported symptom description
            context: Optional context for disambiguation (e.g., "Trauma", "Cardiology")
        
        Returns:
            Canonical symptom name or original text if no match
        
        Example:
            >>> normalizer.normalize("ho un forte mal di testa")
            'Cefalea'
        """
        if not symptom or not isinstance(symptom, str):
            return ""
        
        original = symptom
        
        # Level 0: Preprocessing
        cleaned = self._preprocess(symptom)
        
        if not cleaned:
            return original
        
        # Level 1: Exact match
        if cleaned in self.canonical_kb:
            canonical = self.canonical_kb[cleaned]
            logger.debug(f"Exact match: '{original}' → '{canonical}'")
            return canonical
        
        # Level 2: Fuzzy matching
        canonical_keys = list(self.canonical_kb.keys())
        matches = difflib.get_close_matches(
            cleaned,
            canonical_keys,
            n=1,
            cutoff=self.fuzzy_threshold
        )
        
        if matches:
            matched_key = matches[0]
            canonical = self.canonical_kb[matched_key]
            
            # Compute similarity for logging
            similarity = difflib.SequenceMatcher(None, cleaned, matched_key).ratio()
            
            logger.debug(
                f"Fuzzy match: '{original}' → '{canonical}' "
                f"(similarity: {similarity:.2f}, key: '{matched_key}')"
            )
            
            return canonical
        
        # Level 3: Fallback - no match found
        logger.warning(f"No match found for: '{original}' (cleaned: '{cleaned}')")
        self.unknown_terms.add(original)
        
        return original
    
    def get_unknown_terms(self) -> List[str]:
        """
        Get list of terms that failed normalization.
        
        Returns:
            Sorted list of unknown terms
        """
        return sorted(list(self.unknown_terms))
    
    def add_to_kb(self, symptom_variant: str, canonical: str) -> None:
        """
        Add new symptom variant to knowledge base.
        
        Args:
            symptom_variant: User-reported variant (will be preprocessed)
            canonical: Canonical medical term
        """
        cleaned = self._preprocess(symptom_variant)
        if cleaned:
            self.canonical_kb[cleaned] = canonical
            logger.info(f"Added to KB: '{symptom_variant}' → '{canonical}'")


# ============================================================================
# DIAGNOSIS SANITIZER
# ============================================================================


class DiagnosisSanitizer: 
    """Blocca diagnosi non autorizzate e prescrizioni farmacologiche."""
    FORBIDDEN_PATTERNS = [
        r"\bdiagnosi\b", r"\bprescrivo\b", r"\bterapia\b",
        r"\bhai\s+(la|il|un[\'a]? )\s+\w+",
        r"\bè\s+(sicuramente|probabilmente)\b",
        r"\bprendi\s+\w+\s+mg\b",
        r"\b(hai|sembra che tu abbia|potresti avere)\s+.*\b(infiammazione|infezione|patologia|malattia)\b"
    ]
    
    @staticmethod
    def sanitize(response: TriageResponse) -> TriageResponse:
        text_lower = response.testo.lower()
        for pattern in DiagnosisSanitizer.FORBIDDEN_PATTERNS:
            if re.search(pattern, text_lower):
                logging.critical(f"DIAGNOSI BLOCCATA: {response.testo}")
                response.testo = "In base ai dati raccolti, la situazione merita un approfondimento clinico.  Potresti descrivermi meglio da quanto tempo avverti questi sintomi?"
                response.metadata.confidence = 0.1
                break
        return response


class ModelOrchestrator:
    """
    Orchestratore AI con Fallback Groq -> Gemini. 
    Versione aggiornata per modelli Emilia-Romagna con gestione dinamica anno.
    """
    def __init__(self, groq_key: str = "", gemini_key: str = ""):
        self.groq_client = None
        self.gemini_model = None
        self._executor = ThreadPoolExecutor(max_workers=5)
        self.router = SmartRouter()
        self.symptom_normalizer = SymptomNormalizer()
        self.prompts = self._load_prompts()
        
        g_key = groq_key or st.secrets.get("GROQ_API_KEY", "")
        gem_key = gemini_key or st.secrets.get("GEMINI_API_KEY", "")
        
        self.set_keys(groq=g_key, gemini=gem_key)
        atexit.register(self._cleanup)

    def set_keys(self, groq:  str = "", gemini: str = ""):
        """Configura o aggiorna le chiavi API in runtime."""
        try:
            if groq:
                from groq import AsyncGroq
                self.groq_client = AsyncGroq(api_key=groq)
                logging.info("Groq client initialized")
            
            if gemini: 
                import google.generativeai as genai
                genai.configure(api_key=gemini)
                self.gemini_model = genai.GenerativeModel("gemini-2.0-flash-exp")
                logging.info("Gemini model initialized (gemini-2.0-flash-exp)")
        except Exception as e:
            logging.error(f"Errore configurazione chiavi: {e}")

    def _cleanup(self):
        if hasattr(self, '_executor'):
            self._executor. shutdown(wait=False)

    def _load_prompts(self) -> Dict[str, str]:
        return {
            "base_rules": (
                "Sei l'AI Health Navigator (SIRAYA). NON SEI UN MEDICO.\n"
                "- SINGLE QUESTION POLICY: Poni una sola domanda alla volta.\n"
                "- NO DIAGNOSI: Non fornire diagnosi né ordini.\n"
                "- SLOT FILLING: Estrai dati (età, luogo, sintomi) dai messaggi liberi. Se un dato è già presente, chiedi solo conferma.\n"
                "- FORMATO OPZIONI: Usa sempre opzioni A, B, C per guidare l'utente."
            ),
            "percorso_a": (
                "EMERGENZA (SOSPETTO RED/ORANGE):\n"
                "1. SETUP: Localizzazione Immediata (Salta se nota).\n"
                "2. INDAGINE CLINICA (FAST-TRIAGE): \n"
                "   - VINCOLO: Esegui ALMENO 3 domande rapide specifiche sul sintomo per confermare l'urgenza.\n"
                "   - Argomenti: Irradiazione dolore, difficoltà respiratoria, esordio.\n"
                "   - Nota: Il conteggio delle 3 domande inizia SOLO dopo aver stabilito la Location.\n"
                "3. ESITO: Se confermato, consiglia il PS da master_kb.json, link affollamento e report SBAR."
            ),
            "percorso_b": (
                "SALUTE MENTALE (SOSPETTO BLACK):\n"
                "1. CONSENSO: Richiedi autorizzazione per domande personali.\n"
                "2. INDAGINE CLINICA (VALUTAZIONE RISCHIO):\n"
                "   - Valuta percorsi seguiti, farmaci e rischio immediato (autolesionismo/suicidio).\n"
                "   - VINCOLO: Segui i protocolli KB per escludere l'emergenza.\n"
                "3. ESITO: Se emergenza, 118 e hotline. Se supporto territoriale, richiedi età per routing CSM/NPIA."
            ),
            "percorso_c": (
                "STANDARD (GREEN/YELLOW):\n"
                "1. ANAMNESI BASE: Età, Sesso, Gravidanza, Farmaci (Una alla volta).\n"
                "2. INDAGINE CLINICA (INDAGINE ADATTIVA):\n"
                "   - VINCOLO: Esegui tra 5 e 7 domande di approfondimento clinico basate sul sintomo principale.\n"
                "   - MEDICALIZZAZIONE: Se l'utente usa testo libero, medicalizza il termine e rigenera 3 opzioni A/B/C specifiche.\n"
                "   - Nota: Le domande anamnestiche (Età/Sesso) NON contano nel limite delle 5-7 domande cliniche.\n"
                "3. ESITO: Routing gerarchico (Specialistica -> CAU -> MMG) e report SBAR finale."
            ),
            "disposition_prompt": (
                "FASE SBAR (HANDOVER):\n"
                "Genera il riassunto strutturato obbligatorio:\n"
                "S (Situation): Sintomo e intensità.\n"
                "B (Background): Età, sesso, farmaci.\n"
                "A (Assessment): Red Flags escluse e risposte chiave.\n"
                "R (Recommendation): Struttura suggerita e motivo."
            ),
            "disposition_final_prompt": (
                "FASE FINALE (DISPOSITION):\n"
                "Genera report SBAR strutturato:\n"
                "S (Situation): Sintomo principale + intensità\n"
                "B (Background): Età, sesso, localizzazione, anamnesi\n"
                "A (Assessment): Red flags rilevati, urgenza\n"
                "R (Recommendation): Struttura sanitaria consigliata\n"
                "NO opzioni - solo testo informativo e raccomandazione."
            ),
            "abc_format_instruction": (
                "FORMATO OBBLIGATORIO OPZIONI A/B/C:\n"
                "Presenta sempre 3 opzioni chiare e distinte:\n"
                "a) Prima opzione (più comune/probabile)\n"
                "b) Seconda opzione (alternativa)\n"
                "c) Terza opzione (es. 'Altro' o 'Non sono sicuro/a')\n\n"
                "L'utente può:\n"
                "- Cliccare su un pulsante\n"
                "- Scrivere 'a', 'b' o 'c'\n"
                "- Scrivere testo libero (che tu interpreterai)\n\n"
                "Se l'utente scrive testo libero che non corrisponde alle opzioni, "
                "estrailo come dato in 'dati_estratti' e conferma brevemente."
            )
        }
    
    def _build_context_section(self, collected_data: Dict) -> str:
        """
        Costruisce la sezione del prompt con i dati già raccolti.
        FIX BUG #2: Iniezione esplicita con formato JSON per chiarezza AI
        """
        if not collected_data:
            return "DATI GIÀ RACCOLTI:  Nessuno\n\nINIZIA LA RACCOLTA DATI."
        
        known_slots = []
        
        # Mappatura completa con priorità per Red Flags
        if collected_data.get('LOCATION'):
            known_slots. append(f"Comune: {collected_data['LOCATION']}")
        
        if collected_data.get('CHIEF_COMPLAINT'):
            known_slots.append(f"Sintomo principale: {collected_data['CHIEF_COMPLAINT']}")
        
        if collected_data.get('PAIN_SCALE'):
            known_slots.append(f"Dolore: {collected_data['PAIN_SCALE']}/10")
        
        # FIX CRITICO:  Gestione robusta RED_FLAGS
        if collected_data.get('RED_FLAGS'):
            rf = collected_data['RED_FLAGS']
            if isinstance(rf, str):
                rf_display = rf
            elif isinstance(rf, list):
                rf_display = ', '.join(rf) if rf else 'Nessuno rilevato'
            else:
                rf_display = str(rf)
            known_slots.append(f"Red Flags: {rf_display}")
        
        if collected_data.get('age'):
            known_slots.append(f"Età: {collected_data['age']} anni")
        
        if collected_data.get('sex'):
            known_slots.append(f"Sesso: {collected_data['sex']}")
        
        if collected_data. get('pregnant'):
            known_slots.append(f"Gravidanza:  {collected_data['pregnant']}")
        
        if collected_data.get('medications'):
            known_slots.append(f"Farmaci: {collected_data['medications']}")
        
        # NUOVA SEZIONE: Esportazione JSON per debug AI
        json_export = json.dumps(collected_data, ensure_ascii=False, indent=2)
        
        context = f"""
DATI GIA RACCOLTI (NON RIPETERE QUESTE DOMANDE):

{chr(10).join(known_slots)}

Formato Strutturato (per validazione):
{json_export}

ISTRUZIONE CRITICA:
- Se un dato è presente sopra, NON chiedere nuovamente
- Passa direttamente al prossimo slot mancante
- Se tutti i dati sono completi, genera la raccomandazione finale
"""
        
        return context
    
    def _determine_next_slot(self, collected_data: Dict, current_phase: str) -> str:
        """
        Determina il prossimo slot da riempire seguendo il protocollo triage.
        FIX BUG #2: Gestione intelligente RED_FLAGS
        """
        if not collected_data. get('LOCATION') and current_phase != "DISPOSITION":
            return "Comune di residenza (Emilia-Romagna)"
        
        if not collected_data.get('CHIEF_COMPLAINT') and current_phase != "DISPOSITION":
            return "Sintomo principale (descrizione breve)"
        
        if not collected_data.get('PAIN_SCALE') and current_phase != "DISPOSITION": 
            return "Intensità dolore (scala 1-10, o 'nessun dolore')"
        
        # FIX CRITICO RED_FLAGS:  Verifica se è stringa vuota, lista vuota, o None
        red_flags_data = collected_data.get('RED_FLAGS')
        has_red_flags = False
        
        if red_flags_data:
            if isinstance(red_flags_data, str) and red_flags_data.strip():
                has_red_flags = True
            elif isinstance(red_flags_data, list) and len(red_flags_data) > 0:
                has_red_flags = True
        
        if not has_red_flags and current_phase != "DISPOSITION": 
            return """RED FLAGS (DOMANDA SINGOLA):
            
Fai UNA SOLA domanda tra queste opzioni (scegli la più rilevante):
1. "Hai difficoltà a respirare o dolore al petto?"
2. "Hai avuto febbre alta (>38.5°C) nelle ultime 24 ore?"
3. "Hai notato perdite di sangue insolite?"

NON fare più di una domanda per messaggio. 
Se l'utente risponde NO, considera RED_FLAGS completato e passa all'anamnesi.
"""
        
        if not collected_data.get('age') and current_phase != "DISPOSITION":
            return "Età del paziente"
        
        if current_phase == "DISPOSITION": 
            return "GENERAZIONE_RACCOMANDAZIONE_FINALE"
        
        return "Anamnesi aggiuntiva (farmaci, allergie, condizioni croniche)"
    
    def _check_emergency_triggers(self, user_message: str, collected_data: Dict) -> Optional[Dict]:
        """
        Rileva trigger di emergenza in tempo reale.
        Integrazione con sistema di emergenza.
        """
        if not user_message: 
            return None
        
        text_lower = user_message.lower().strip()
        
        red_keywords = [
            "dolore toracico", "dolore petto", "oppressione torace",
            "non riesco respirare", "non riesco a respirare", "soffoco", "difficoltà respiratoria grave",
            "perdita di coscienza", "svenuto", "svenimento",
            "convulsioni", "crisi convulsiva",
            "emorragia massiva", "sangue abbondante",
            "paralisi", "metà corpo bloccata"
        ]
        
        for keyword in red_keywords:
            if keyword in text_lower: 
                logger.error(f"RED EMERGENCY detected: '{keyword}'")
                return {
                    "testo": "Rilevata possibile emergenza.  Chiama immediatamente il 118.",
                    "tipo_domanda": "text",
                    "fase_corrente": "EMERGENCY_OVERRIDE",
                    "opzioni": None,
                    "dati_estratti": {},
                    "metadata": {
                        "urgenza": 5,
                        "area": "Emergenza",
                        "red_flags": [keyword],
                        "confidence": 1.0,
                        "fallback_used": False
                    }
                }
        
        return None

    def _get_system_prompt(self, path:  str, phase: str, collected_data: Dict = None, is_first_message: bool = False) -> str:
        """
        Genera system prompt dinamico con contesto dei dati già raccolti.
        
        Args:
            path: Percorso triage (A/B/C)
            phase: Fase corrente
            collected_data: Dati già raccolti
            is_first_message: True se primo contatto
        """
        if collected_data is None:
            collected_data = {}
        
        if is_first_message:
            return f"""
{self.prompts['base_rules']}

PRIMO CONTATTO - ROUTING INTELLIGENTE: 
Analizza il messaggio dell'utente e determina l'intento: 

1. **TRIAGE PATH** (Percorso A/B/C):
   - Sintomi attivi (dolore, febbre, trauma)
   - Richieste urgenti ("mi fa male", "ho bisogno di cure")
   → Inizia raccolta dati:  Location → Sintomo → Urgenza

2. **INFO PATH** (Servizi ASL):
   - Domande generiche ("dove trovo.. .", "orari farmacie")
   - Chiarimenti ("cosa fai? ", "come funziona?")
   → Rispondi direttamente senza raccogliere dati clinici

RISPONDI IN JSON:
{{
    "testo": "messaggio per l'utente",
    "tipo_domanda": "text|info_request",
    "fase_corrente": "INTENT_DETECTION|LOCATION|INFO_SERVICES",
    "dati_estratti": {{}},
    "metadata": {{ "urgenza": 1, "area": "Generale", "confidence": 0.8, "fallback_used": false }}
}}
"""
        
        context_section = self._build_context_section(collected_data)
        next_slot_info = self._determine_next_slot(collected_data, phase)
        path_instruction = self.prompts.get(f"percorso_{path.lower()}", self.prompts["percorso_c"])
        
        # Aggiungi istruzioni A/B/C per fasi non-DISPOSITION
        abc_instruction = ""
        if phase != "DISPOSITION" and phase != "LOCATION":
            abc_instruction = f"\n\n{self.prompts['abc_format_instruction']}"
        
        if phase == "DISPOSITION":
            path_instruction = self.prompts["disposition_prompt"]
            abc_instruction = ""  # No options for final disposition
        
        return f"""
{self.prompts['base_rules']}

CONTESTO MEMORIA (NON CHIEDERE NUOVAMENTE):
{context_section}

OBIETTIVO ATTUALE: {next_slot_info}
DIRETTIVE: {path_instruction}
FASE: {phase} | PERCORSO: {path}
{abc_instruction}

ESTRAZIONE AUTOMATICA: 
Se l'utente fornisce spontaneamente dati (es. "Sono a Bologna e mi fa male la testa"):
- Popola "dati_estratti" con TUTTI i dati rilevati
- Conferma brevemente e passa alla prossima domanda

FORMATO RISPOSTA JSON:
{{
    "testo": "domanda + opzioni formattate (se fase richiede survey)",
    "tipo_domanda": "survey|scale|text|confirmation",
    "opzioni": ["Testo opzione A", "Testo opzione B", "Testo opzione C"] o null,
    "fase_corrente": "{phase}",
    "dati_estratti": {{
        "LOCATION": "nome_comune" (se presente),
        "CHIEF_COMPLAINT": "sintomo" (se presente),
        "PAIN_SCALE": 1-10 (se presente),
        "RED_FLAGS": ["lista", "sintomi"] (se presenti),
        "age": numero (se presente),
        "sex": "M|F" (se presente),
        "medications": "testo" (se presente)
    }},
    "metadata": {{ "urgenza": 1-5, "area": "...", "confidence": 0.0-1.0, "fallback_used": false }}
}}

ESEMPI:
Per RED_FLAGS: 
{{
    "testo": "Hai avuto febbre alta (sopra 38.5°C) nelle ultime 24 ore?",
    "tipo_domanda": "survey",
    "opzioni": ["Sì, febbre superiore a 38.5°C", "Febbre leggera (sotto 38.5°C)", "No, nessuna febbre"],
    ...
}}

Per CHIEF_COMPLAINT:
{{
    "testo": "Qual è il sintomo che ti preoccupa di più?",
    "tipo_domanda": "survey",
    "opzioni": ["Dolore", "Febbre", "Altro sintomo (specifica)"],
    ...
}}
"""

    async def call_ai_streaming(self, messages: List[Dict], path: str, phase: str,
                                 collected_data: Dict = None, is_first_message: bool = False) -> AsyncGenerator[Union[str, TriageResponse], None]:
        """
        Metodo principale con logging dettagliato e modelli aggiornati.
        
        Args:
            messages: Lista messaggi della conversazione
            path: Percorso triage (A/B/C)
            phase: Fase corrente
            collected_data: Dati già raccolti
            is_first_message: True se primo contatto
        
        Yields:
            str: Token di testo per streaming
            TriageResponse:  Oggetto finale con metadati
        """
        if collected_data is None:
            collected_data = {}
        
        if messages: 
            last_user_msg = next((m['content'] for m in reversed(messages) if m.get('role') == 'user'), "")
            emergency_response = self._check_emergency_triggers(last_user_msg, collected_data)
            if emergency_response:
                logger.warning("Emergency override attivato")
                yield emergency_response['testo']
                yield TriageResponse(**emergency_response)
                return
        
        system_msg = self._get_system_prompt(path, phase, collected_data, is_first_message)
        api_messages = [{"role": "system", "content":  system_msg}] + messages[-5:]
        full_response_str = ""
        success = False

        logger.info(f"call_ai_streaming START | phase={phase}, path={path}, collected_keys={list(collected_data.keys())}")
        logger.info(f"Groq disponibile: {self.groq_client is not None}")
        logger.info(f"Gemini disponibile: {self.gemini_model is not None}")

        if self.groq_client:
            try:
                logger.info("Tentativo Groq con llama-3.3-70b-versatile...")
                stream = await asyncio.wait_for(
                    self.groq_client.chat. completions.create(
                        model="llama-3.3-70b-versatile",
                        messages=api_messages,
                        temperature=0.1,
                        stream=True,
                        response_format={"type": "json_object"}
                    ), timeout=60.0
                )
                
                logger.info("Groq stream ricevuto, lettura in corso...")
                async for chunk in stream:
                    token = chunk.choices[0].delta.content or ""
                    full_response_str += token
                
                logger.info(f"Groq completato | Lunghezza: {len(full_response_str)} char")
                success = True
                
            except asyncio.TimeoutError:
                logger.error("Groq TIMEOUT (60 secondi)")
            except Exception as e:
                logger.error(f"Groq ERROR: {type(e).__name__} - {str(e)}")

        if not success and self.gemini_model:
            try:
                logger.info("Tentativo fallback Gemini...")
                def _gem_call():
                    prompt = "\n".join([f"{m['role']}: {m['content']}" for m in api_messages])
                    res = self.gemini_model. generate_content(prompt)
                    return res.text
                
                full_response_str = await asyncio.get_event_loop().run_in_executor(self._executor, _gem_call)
                logger.info(f"Gemini completato | Lunghezza:  {len(full_response_str)} char")
                success = True
                
            except Exception as e:
                logger.error(f"Gemini ERROR:  {type(e).__name__} - {str(e)}")

        if success and full_response_str:
            try:
                logger.info("Inizio parsing JSON...")
                clean_json = re.sub(r"```json\n? |```", "", full_response_str).strip()
                logger.debug(f"JSON pulito (primi 200 char): {clean_json[:200]}")
                
                data = json.loads(clean_json)
                response_obj = TriageResponse(**data)
                response_obj = DiagnosisSanitizer. sanitize(response_obj)

                if phase == "DISPOSITION":
                    loc = st.session_state.get("collected_data", {}).get("LOCATION", "Bologna")
                    urgenza = response_obj.metadata.urgenza
                    area = response_obj.metadata.area
                    
                    structure = self. router.route(loc, urgenza, area)
                    
                    st.session_state.collected_data['DISPOSITION'] = {
                        'type': structure['tipo'],
                        'urgency': urgenza,
                        'facility_name': structure['nome'],
                        'note': structure.get('note', ''),
                        'distance': structure.get('distance_km')
                    }
                    
                    response_obj.testo += f"\n\nStruttura consigliata: {structure['nome']}\n{structure. get('note', '')}"

                logger.info(f"Parsing completato | Testo: {len(response_obj.testo)} char")
                yield response_obj.testo
                yield response_obj
                return
                
            except json.JSONDecodeError as e:
                logger.error(f"JSON DECODE ERROR: {e}")
                logger.error(f"JSON problematico: {full_response_str[: 500]}")
            except ValidationError as e:
                logger. error(f"PYDANTIC VALIDATION ERROR: {e}")
            except Exception as e:
                logger.error(f"PARSING ERROR: {type(e).__name__} - {str(e)}")

        logger.warning("Restituzione fallback generico")
        fallback = self._get_safe_fallback_response()
        yield fallback. testo
        yield fallback

    def _medicalize_and_regenerate_options(self, free_text: str, phase: str, collected_data: Dict) -> List[str]:
        """
        Medicalizza testo libero e rigenera 3 opzioni A/B/C specifiche.
        
        Args:
            free_text: Testo libero dell'utente
            phase: Fase corrente
            collected_data: Dati già raccolti
        
        Returns:
            List[str]: 3 opzioni medicalizzate A/B/C
        """
        # Normalizza sintomo
        normalized = self.symptom_normalizer.normalize(free_text)
        
        logger.info(f"🔬 Medicalizzazione: '{free_text}' → '{normalized}'")
        
        # Genera 3 opzioni basate su sintomo normalizzato
        # Opzione A: Variante più grave
        # Opzione B: Variante moderata (normalizzata)
        # Opzione C: Variante lieve o "Nessuno"
        
        if phase == "CHIEF_COMPLAINT":
            return [
                f"{normalized} intenso/acuto",
                f"{normalized} moderato",
                f"{normalized} lieve o altro sintomo"
            ]
        elif phase == "RED_FLAGS":
            return [
                f"Sì, ho {normalized}",
                f"{normalized} leggero",
                "No, nessun sintomo critico"
            ]
        elif phase == "PAIN_ASSESSMENT":
            # Estrai numero se presente
            import re
            numbers = re.findall(r'\d+', free_text)
            if numbers:
                pain_num = int(numbers[0])
                if pain_num >= 7:
                    return ["9-10 Insopportabile", "7-8 Forte", "4-6 Moderato"]
                elif pain_num >= 4:
                    return ["7-8 Forte", "4-6 Moderato", "1-3 Lieve"]
                else:
                    return ["4-6 Moderato", "1-3 Lieve", "Nessun dolore"]
            return ["7-10 Forte/Insopportabile", "4-6 Moderato", "1-3 Lieve"]
        else:
            # Default: 3 opzioni generiche
            return [
                f"Sì, {normalized}",
                f"{normalized} parziale",
                "No, nessun problema"
            ]
    
    def _get_safe_fallback_response(self) -> TriageResponse:
        return TriageResponse(
            testo="Sto analizzando i dati raccolti. Potresti descrivere con più precisione come ti senti in questo momento?",
            tipo_domanda=QuestionType.TEXT,
            fase_corrente="ANAMNESIS",
            dati_estratti={},
            metadata=TriageMetadata(urgenza=3, area="Generale", confidence=0.0, fallback_used=True)
        )

    def is_available(self) -> bool:
        """Controlla se almeno uno dei servizi è configurato."""
        return bool(self.groq_client or self.gemini_model)
    
    def process_message(self, user_message: str, session_id: str = "") -> str:
        """
        Metodo sincrono per processare un messaggio utente.
        Wrapper per call_ai_streaming usato da frontend.py.
        
        Args:
            user_message: Messaggio utente da processare
            session_id: ID sessione (opzionale, per tracking)
        
        Returns:
            str: Risposta completa del bot
        """
        # Recupera stato dalla sessione
        collected_data = st.session_state.get("collected_data", {})
        messages = st.session_state.get("messages", [])
        
        # Determina fase corrente
        current_step = st.session_state.get("current_step", "INIT")
        is_first = len(messages) <= 1
        
        # Determina path (A = emergenza, B = mental health, C = standard)
        path = "C"  # Default: standard
        if collected_data.get("RED_FLAGS"):
            path = "A"
        
        # Aggiungi messaggio utente alla lista
        api_messages = [{"role": m["role"], "content": m["content"]} for m in messages]
        api_messages.append({"role": "user", "content": user_message})
        
        # Esegui chiamata AI in modo sincrono
        full_response = ""
        
        try:
            # Crea event loop se necessario
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            # Esegui async generator e raccogli risultato
            async def collect_response():
                nonlocal full_response
                response_obj = None
                
                async for item in self.call_ai_streaming(
                    api_messages, path, current_step, 
                    collected_data, is_first
                ):
                    if isinstance(item, str):
                        full_response = item
                    elif isinstance(item, TriageResponse):
                        response_obj = item
                        full_response = item.testo
                        
                        # Aggiorna collected_data con dati estratti
                        if item.dati_estratti:
                            for key, value in item.dati_estratti.items():
                                if value:
                                    st.session_state.collected_data[key] = value
                        
                        # Aggiorna fase corrente
                        if item.fase_corrente:
                            st.session_state.current_step = item.fase_corrente
                
                return full_response
            
            # Esegui
            if loop.is_running():
                # Se già in un loop async (es. Streamlit), usa thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, collect_response())
                    full_response = future.result(timeout=90)
            else:
                full_response = loop.run_until_complete(collect_response())
                
        except Exception as e:
            logger.error(f"process_message ERROR: {e}")
            full_response = "Mi scuso, si è verificato un errore tecnico. Potresti ripetere la domanda?"
        
        return full_response


# ============================================================================
# ALIAS PER FRONTEND.PY
# ============================================================================

# Orchestrator è l'alias usato da frontend.py
Orchestrator = ModelOrchestrator