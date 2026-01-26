# smart_router.py - Risk Classification + Branching + Path A/B/C Routing
"""
Enhanced smart router with FSM integration and Path-based routing.

Features:
- Initial urgency classification with UrgencyScore
- Branch detection (TRIAGE vs INFORMAZIONI)
- Path A/B/C assignment based on keywords
- FSM phase transitions with route_to_phase
- Hierarchical routing with Path-specific rules
- Mental health (Path B) and emergency (Path A) detection

Classes:
    UrgencyScore: Dataclass for urgency classification results
    SmartRouter: Main routing engine with FSM support
"""

import json
import logging
import re
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass

from models import TriageState, TriagePath, TriagePhase, TriageBranch

logger = logging.getLogger(__name__)

# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class UrgencyScore:
    """
    Result of initial urgency classification.
    
    Attributes:
        score: Urgency level 1-5 (1=low, 5=critical)
        assigned_path: Path A/B/C based on classification
        assigned_branch: TRIAGE or INFORMAZIONI
        rationale: Explanation for classification
        detected_red_flags: List of critical red flags found
        requires_immediate_118: True if 118 should be called immediately
    """
    score: int  # 1-5
    assigned_path: TriagePath
    assigned_branch: TriageBranch
    rationale: str
    detected_red_flags: List[str]
    requires_immediate_118: bool


# ============================================================================
# KEYWORD DATABASES
# ============================================================================

# Critical red flags requiring 118 immediately
CRITICAL_RED_FLAGS = {
    r"dolore\s+(toracico|petto|al\s+petto)": "Dolore toracico",
    r"oppressione\s+torace": "Dolore toracico",
    r"non\s+riesco\s+(a\s+)?respirare": "Dispnea grave",
    r"soffoco": "Dispnea grave",
    r"perdita\s+di\s+coscienza": "Perdita coscienza",
    r"svenuto|svenimento": "Perdita coscienza",
    r"convulsioni?|crisi\s+convulsiva": "Convulsioni",
    r"emorragia\s+massiva": "Emorragia massiva",
    r"sangue\s+abbondante": "Emorragia massiva",
    r"paralisi": "Paralisi",
    r"\b(braccio|gamba)\s+non\s+si\s+muove\b": "Paralisi"
}

# High-priority red flags for Path A (fast-track)
HIGH_RED_FLAGS = {
    r"febbre\s+(alta|39|40)": "Febbre >39°C",
    r"trauma\s+cranico": "Trauma cranico",
    r"battuto\s+(forte\s+)?testa": "Trauma cranico",
    r"vomito\s+(continuo|persistente|sangue)": "Vomito persistente",
    r"dolore\s+addominale\s+acuto": "Dolore addominale acuto",
    r"dolore\s+pancia\s+(molto\s+)?forte": "Dolore addominale acuto",
    r"sanguinamento": "Sanguinamento"
}

# Mental health keywords for Path B
MENTAL_HEALTH_KEYWORDS = [
    "ansia", "ansioso", "ansiosa", "attacco di panico", "panico",
    "depressione", "depresso", "depressa", "triste", "tristezza",
    "pensieri suicidi", "suicidio", "togliermi la vita",
    "autolesionismo", "tagliarmi", "farmi male",
    "stress", "burn out", "burnout", "esaurimento",
    "non ce la faccio più", "voglio morire"
]

# Informational keywords (non-triage)
INFO_KEYWORDS = [
    "orari", "orario", "quando apre", "quando chiude",
    "farmacia", "farmacie di turno",
    "dove trovo", "dov'è", "come arrivo",
    "come funziona", "cos'è", "cosa fa",
    "prenot", "appuntamento",
    "numero", "telefono", "contatto"
]


# ============================================================================
# MAIN ROUTER CLASS
# ============================================================================

class SmartRouter:
    """
    Enhanced routing engine with FSM and Path A/B/C support.
    
    Attributes:
        kb: Knowledge base loaded from master_kb.json
        structures_kb: Preprocessed facilities dictionary
    """
    
    def __init__(self, kb_path: str = "master_kb.json"):
        """
        Initialize router with knowledge base.
        
        Args:
            kb_path: Path to master_kb.json with facility data
        """
        self.kb = self._load_kb(kb_path)
        self.structures_kb = self._preprocess_structures()
        logger.info(f"✅ SmartRouter initialized with {len(self.structures_kb)} facilities")
    
    def _load_kb(self, path: str) -> Dict:
        """Load knowledge base from JSON file."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"KB {path} not found: {e}")
            return {"facilities": []}
    
    def _preprocess_structures(self) -> Dict[str, List[Dict]]:
        """
        Preprocess facilities by type for faster lookup.
        
        Returns:
            Dict mapping facility type to list of facilities
        """
        structures = {}
        facilities = self.kb.get("facilities", [])
        
        for facility in facilities:
            facility_type = facility.get("tipologia", "Unknown")
            if facility_type not in structures:
                structures[facility_type] = []
            structures[facility_type].append(facility)
        
        return structures
    
    # ========================================================================
    # 1. CLASSIFY INITIAL URGENCY - Entry Point
    # ========================================================================
    
    def classify_initial_urgency(self, first_message: str) -> UrgencyScore:
        """
        Classify initial urgency and assign Path/Branch from first message.
        
        Logic Flow (in priority order):
        1. Scan INFO_KEYWORDS → Branch INFORMAZIONI (no triage)
        2. Scan CRITICAL_RED_FLAGS → 118 immediate + Path A
        3. Scan HIGH_RED_FLAGS → Path A (Fast-Track)
        4. Scan MENTAL_HEALTH_KEYWORDS → Path B
        5. Mild symptoms (headache, cold, cough) → Path C score 2
        6. Default → Path C score 3
        
        Args:
            first_message: User's first message
        
        Returns:
            UrgencyScore with classification results
        
        Example:
            >>> router = SmartRouter()
            >>> score = router.classify_initial_urgency("ho dolore al petto")
            >>> score.requires_immediate_118
            True
            >>> score.assigned_path
            <TriagePath.A: 'EMERGENZA_FISICA'>
        """
        if not first_message or not isinstance(first_message, str):
            # Default classification
            return UrgencyScore(
                score=3,
                assigned_path=TriagePath.C,
                assigned_branch=TriageBranch.TRIAGE,
                rationale="Messaggio vuoto - default Path C",
                detected_red_flags=[],
                requires_immediate_118=False
            )
        
        text_lower = first_message.lower().strip()
        detected_flags = []
        
        logger.info(f"🔍 Classifying: '{first_message}'")
        
        # === STEP 1: Check INFO keywords (Branch INFORMAZIONI) ===
        for keyword in INFO_KEYWORDS:
            if keyword in text_lower:
                logger.info(f"📋 INFO keyword detected: '{keyword}' → Branch INFORMAZIONI")
                return UrgencyScore(
                    score=1,
                    assigned_path=TriagePath.C,  # Nominal path
                    assigned_branch=TriageBranch.INFORMAZIONI,
                    rationale=f"Informational request detected: '{keyword}'",
                    detected_red_flags=[],
                    requires_immediate_118=False
                )
        
        # === STEP 2: Check CRITICAL red flags (118 immediate) ===
        for pattern, flag_name in CRITICAL_RED_FLAGS.items():
            if re.search(pattern, text_lower):
                detected_flags.append(flag_name)
                logger.error(f"🚨 CRITICAL RED FLAG: {flag_name} → 118 IMMEDIATE")
                return UrgencyScore(
                    score=5,
                    assigned_path=TriagePath.A,
                    assigned_branch=TriageBranch.TRIAGE,
                    rationale=f"Critical emergency: {flag_name}",
                    detected_red_flags=detected_flags,
                    requires_immediate_118=True
                )
        
        # === STEP 3: Check HIGH red flags (Path A fast-track) ===
        for pattern, flag_name in HIGH_RED_FLAGS.items():
            if re.search(pattern, text_lower):
                detected_flags.append(flag_name)
                logger.warning(f"⚠️ HIGH RED FLAG: {flag_name} → Path A")
                return UrgencyScore(
                    score=4,
                    assigned_path=TriagePath.A,
                    assigned_branch=TriageBranch.TRIAGE,
                    rationale=f"High urgency: {flag_name}",
                    detected_red_flags=detected_flags,
                    requires_immediate_118=False
                )
        
        # === STEP 4: Check MENTAL HEALTH keywords (Path B) ===
        for keyword in MENTAL_HEALTH_KEYWORDS:
            if keyword in text_lower:
                logger.info(f"🧠 MENTAL HEALTH keyword: '{keyword}' → Path B")
                return UrgencyScore(
                    score=3,
                    assigned_path=TriagePath.B,
                    assigned_branch=TriageBranch.TRIAGE,
                    rationale=f"Mental health concern: '{keyword}'",
                    detected_red_flags=[],
                    requires_immediate_118=False
                )
        
        # === STEP 5: Check MILD symptoms (Path C low urgency) ===
        mild_symptoms = [
            "mal di testa", "cefalea", "raffreddore", "tosse",
            "naso chiuso", "febbre bassa", "febbre leggera"
        ]
        
        for symptom in mild_symptoms:
            if symptom in text_lower:
                logger.info(f"🟢 MILD symptom: '{symptom}' → Path C low urgency")
                return UrgencyScore(
                    score=2,
                    assigned_path=TriagePath.C,
                    assigned_branch=TriageBranch.TRIAGE,
                    rationale=f"Mild symptom: {symptom}",
                    detected_red_flags=[],
                    requires_immediate_118=False
                )
        
        # === STEP 6: DEFAULT (Path C standard) ===
        logger.info("🔵 DEFAULT classification → Path C standard")
        return UrgencyScore(
            score=3,
            assigned_path=TriagePath.C,
            assigned_branch=TriageBranch.TRIAGE,
            rationale="Standard triage path",
            detected_red_flags=[],
            requires_immediate_118=False
        )
    
    # ========================================================================
    # 2. ROUTE TO PHASE - FSM Transition Logic
    # ========================================================================
    
    def route_to_phase(self, state: TriageState) -> Tuple[TriagePhase, str]:
        """
        Determine next phase based on current state and Path.
        
        FSM Logic:
        
        **Path A (max 3 questions):**
        1. LOCATION
        2. CHIEF_COMPLAINT
        3. RED_FLAGS
        4. DISPOSITION
        
        **Path B (with consent):**
        1. Consent check (if not given)
        2. LOCATION
        3. DEMOGRAPHICS (age for CSM/NPIA)
        4. CHIEF_COMPLAINT (nature of distress)
        5. Risk assessment (self-harm)
        6. DISPOSITION (hierarchical: Consultorio > CSM > MMG)
        
        **Path C (complete protocol):**
        1. LOCATION
        2. CHIEF_COMPLAINT
        3. PAIN_ASSESSMENT (1-10 scale)
        4. RED_FLAGS
        5. DEMOGRAPHICS (age, sex, pregnancy)
        6. ANAMNESIS (meds, allergies)
        7. DISPOSITION
        
        Args:
            state: Current TriageState
        
        Returns:
            Tuple of (next_phase, prompt_message)
        """
        # === EMERGENCY OVERRIDE ===
        if state.has_critical_red_flags():
            logger.critical("🚨 EMERGENCY OVERRIDE: Critical red flags detected")
            return (
                TriagePhase.EMERGENCY_OVERRIDE,
                "EMERGENZA: Chiama immediatamente il 118"
            )
        
        # === PATH A: Fast-Triage (3-4 domande) ===
        if state.assigned_path == TriagePath.A:
            # Skip location se già estratta da slot filling
            if not state.patient_info.location:
                return (TriagePhase.LOCATION, "In quale comune ti trovi? (Risposta rapida)")
            
            # Fast-Triage Domanda 1: Irradiazione dolore (se dolore toracico)
            if state.clinical_data.chief_complaint and "dolore" in state.clinical_data.chief_complaint.lower():
                if not state.clinical_data.red_flags or len(state.clinical_data.red_flags) == 0:
                    return (TriagePhase.RED_FLAGS, "Il dolore si irradia al braccio o alla mascella? (Opzioni: SI / NO)")
            
            # Fast-Triage Domanda 2: Sintomo principale (se non già estratto)
            if not state.clinical_data.chief_complaint:
                return (TriagePhase.CHIEF_COMPLAINT, "Descrivi brevemente il sintomo principale")
            
            # Fast-Triage Domanda 3: Red Flags (se non già completato)
            if not state.clinical_data.red_flags or len(state.clinical_data.red_flags) == 0:
                return (TriagePhase.RED_FLAGS, "Hai difficoltà a respirare o dolore al petto? (Opzioni: SI / NO)")
            
            # Path A completo → DISPOSITION
            return (TriagePhase.DISPOSITION, "Genero raccomandazione...")
        
        # === PATH B: Salute Mentale (con consenso e valutazione rischio) ===
        elif state.assigned_path == TriagePath.B:
            # Fase 1: Consenso
            if not state.consent_given:
                return (
                    TriagePhase.INTENT_DETECTION,
                    "Mi sembra di capire che stai attraversando un momento difficile. "
                    "Se sei d'accordo, vorrei farti alcune domande personali per capire come esserti utile. "
                    "(Opzioni: ACCETTO / NO)"
                )
            
            # Fase 2: Percorsi/Farmaci
            if not state.clinical_data.medications and not hasattr(state.clinical_data, 'treatment_history'):
                return (
                    TriagePhase.ANAMNESIS,
                    "Hai già intrapreso percorsi terapeutici o stai assumendo farmaci? (Input aperto)"
                )
            
            # Fase 3: Valutazione Rischio (usa protocolli KB)
            if not state.clinical_data.red_flags or len(state.clinical_data.red_flags) == 0:
                return (
                    TriagePhase.RED_FLAGS,
                    "Valutazione rischio: domande basate su protocolli Knowledge Base per confermare gravità o escluderla."
                )
            
            # Fase 4: Location (per routing CSM/Consultorio)
            if not state.patient_info.location:
                return (TriagePhase.LOCATION, "In quale comune ti trovi? (Necessario per indirizzarti al servizio giusto)")
            
            # Fase 5: Età (per routing NPIA vs CSM)
            if not state.patient_info.age:
                return (TriagePhase.DEMOGRAPHICS, "Quanti anni hai? (Necessario per indirizzarti al servizio giusto)")
            
            # Path B completo → DISPOSITION (CSM/Consultorio/NPIA)
            return (TriagePhase.DISPOSITION, "Genero raccomandazione...")
        
        # === PATH C: Standard Protocol (5-7 domande per Yellow/Green) ===
        else:
            # Conta domande fatte (per limitare a 5-7)
            question_count = 0
            if state.patient_info.location:
                question_count += 1
            if state.clinical_data.chief_complaint:
                question_count += 1
            if state.clinical_data.pain_scale is not None:
                question_count += 1
            if state.clinical_data.red_flags and len(state.clinical_data.red_flags) > 0:
                question_count += 1
            if state.patient_info.age:
                question_count += 1
            if state.clinical_data.medications:
                question_count += 1
            
            # Fase 1: Localizzazione
            if not state.patient_info.location:
                return (TriagePhase.LOCATION, "In quale comune dell'Emilia-Romagna ti trovi?")
            
            # Fase 2: Sintomo principale
            if not state.clinical_data.chief_complaint:
                return (TriagePhase.CHIEF_COMPLAINT, "Qual è il sintomo che ti preoccupa?")
            
            # Fase 3: Scala dolore (con opzioni 1-10)
            if state.clinical_data.pain_scale is None:
                return (
                    TriagePhase.PAIN_ASSESSMENT,
                    "In una scala da 1 a 10, quanto è forte il dolore? "
                    "(Opzioni: 1-3 Lieve, 4-6 Moderato, 7-8 Forte, 9-10 Insopportabile)"
                )
            
            # Fase 4-7: Triage adattivo (5-7 domande totali)
            # Se question_count < 5, continua con domande basate su protocolli KB
            if question_count < 5:
                # Domande basate su protocolli Knowledge Base con opzioni A/B/C
                if not state.clinical_data.red_flags or len(state.clinical_data.red_flags) == 0:
                    return (
                        TriagePhase.RED_FLAGS,
                        "Domanda triage basata su protocolli KB (opzioni A/B/C). "
                        "Se testo libero → medicalizza e rigenera 3 opzioni specifiche."
                    )
            
            # Se question_count >= 5 ma < 7, continua solo se sintomi aggiuntivi
            if question_count < 7:
                # Anamnesi base
                if not state.patient_info.age:
                    return (TriagePhase.DEMOGRAPHICS, "Quanti anni hai?")
                
                if not state.clinical_data.medications:
                    return (TriagePhase.ANAMNESIS, "Prendi farmaci regolarmente? (Opzioni A/B/C)")
            
            # Path C completo (5-7 domande) → DISPOSITION
            return (TriagePhase.DISPOSITION, "Genero raccomandazione...")
    
    # ========================================================================
    # 3. ROUTE - Hierarchical Facility Routing
    # ========================================================================
    
    def route(
        self,
        location: str,
        urgency: int,
        area: str,
        path: Optional[TriagePath] = None
    ) -> Dict:
        """
        Route to appropriate healthcare facility with Path-specific logic.
        
        Hierarchical Routing (Path B and C):
        - Urgency 4-5 → PS (Emergency Department)
        - Urgency 2-3 → Search KB: Specialized Centers > CAU > MMG (fallback)
        - Urgency 1 → Telemedicine > MMG
        
        Path B Specific (Mental Health):
        - Age < 18 → NPIA (Child Neuropsychiatry)
        - Age >= 18 → CSM (Mental Health Center)
        - Consultori for relationship issues
        
        Args:
            location: Patient's city/town
            urgency: Urgency level 1-5
            area: Clinical area (e.g., "Psichiatria", "Generale")
            path: Optional TriagePath for Path-specific routing
        
        Returns:
            Dict with: tipo, nome, note, distance_km
        """
        loc = location.lower().strip() if location else ""
        
        logger.info(f"🗺️ Routing: location={location}, urgency={urgency}, area={area}, path={path}")
        
        # === CRITICAL URGENCY → PS (always) ===
        if urgency >= 4:
            logger.info(f"🚨 Routing to PS for urgency {urgency}")
            return {
                "tipo": "PS",
                "nome": "Pronto Soccorso",
                "note": "Recati immediatamente in ospedale o chiama il 118.",
                "distance_km": None
            }
        
        # === PATH B: MENTAL HEALTH SPECIFIC ===
        if path == TriagePath.B or "Psichiatria" in area or "Mentale" in area:
            logger.info(f"🧠 Mental health routing for area: {area}")
            return {
                "tipo": "CSM",
                "nome": "Centro di Salute Mentale",
                "note": "Contatta il servizio territoriale per una valutazione. "
                        "Per emergenze: 1522 (violenza), Telefono Amico 02 2327 2327",
                "distance_km": None
            }
        
        # === GYNECOLOGY/OBSTETRICS → CONSULTORIO ===
        if "Ginecologia" in area or "Ostetricia" in area or "Gravidanza" in area:
            logger.info(f"👶 Routing to Consultorio for area: {area}")
            return {
                "tipo": "Consultorio",
                "nome": "Consultorio Familiare",
                "note": "Prenota una visita presso il consultorio di zona.",
                "distance_km": None
            }
        
        # === ADDICTIONS → SerD ===
        if "Dipendenze" in area or "Tossicodipendenza" in area or "Alcol" in area:
            logger.info(f"💊 Routing to SerD for area: {area}")
            return {
                "tipo": "SerD",
                "nome": "SerD (Servizio Dipendenze)",
                "note": "Accesso diretto o tramite MMG per supporto specialistico.",
                "distance_km": None
            }
        
        # === MODERATE URGENCY (3) → CAU (ENHANCED) ===
        if urgency == 3:
            logger.info(f"⚡ Routing to CAU (potenziato) for urgency {urgency}")
            return {
                "tipo": "CAU",
                "nome": "CAU (Continuità Assistenziale Urgenze)",
                "note": (
                    "Centro di Assistenza Urgenza per valutazioni senza appuntamento. "
                    "**AGGIORNAMENTO**: I CAU dell'Emilia-Romagna ora offrono "
                    "accesso h24, servizi diagnostici rapidi (ECG, radiologia di base) "
                    "e telemedicina. Trova il CAU più vicino tramite il numero unico 116117 "
                    "o l'app ER Salute."
                ),
                "distance_km": None
            }
        
        # === URGENCY 2 → SEARCH SPECIALIZED SERVICES FIRST ===
        if urgency == 2:
            logger.info(f"🔍 Searching specialized district services for area: {area}")
            
            # Try to find specialized service in knowledge base
            specialized_service = self._search_specialized_service(location, area)
            if specialized_service:
                return specialized_service
            
            # If no specialized service, suggest CAU for minor urgency
            logger.info(f"No specialized service found, routing to CAU")
            return {
                "tipo": "CAU",
                "nome": "CAU (Continuità Assistenziale Urgenze)",
                "note": (
                    "Centro di Assistenza Urgenza per valutazioni senza appuntamento. "
                    "Numero unico 116117 o app ER Salute."
                ),
                "distance_km": None
            }
        
        # === FALLBACK → MMG (General Practitioner) ===
        logger.info(f"🩺 Routing to MMG (fallback) for urgency {urgency}, area {area}")
        return {
            "tipo": "MMG",
            "nome": "Medico di Medicina Generale",
            "note": "Contatta il tuo medico di base per una valutazione nei prossimi giorni.",
            "distance_km": None
        }
    
    def _search_specialized_service(self, location: str, area: str) -> Optional[Dict]:
        """
        Search for specialized district services in knowledge base.
        
        Hierarchical Search:
        1. Poliambulatori specialistici (es. medicazioni, prelievi)
        2. Centri dedicati (SerD, Consultori, Diabetologia)
        3. None (fallback to CAU or MMG)
        
        Args:
            location: Patient's city/town
            area: Clinical area
        
        Returns:
            Dict with facility info or None
        """
        # Mapping area → facility type in KB
        area_to_service = {
            "Medicazioni": "poliambulatorio",
            "Prelievi": "poliambulatorio",
            "Vaccinazioni": "poliambulatorio",
            "Diabetologia": "ambulatorio_diabetologia",
            "Cardiologia": "ambulatorio_cardiologia",
            "Ortopedia": "ambulatorio_ortopedia"
        }
        
        service_type = area_to_service.get(area)
        if not service_type:
            return None
        
        # Search in knowledge base
        facilities = self.structures_kb.get(service_type, [])
        
        # Filter by location (fuzzy match)
        location_lower = location.lower() if location else ""
        for facility in facilities:
            facility_comune = facility.get("comune", "").lower()
            if location_lower in facility_comune or facility_comune in location_lower:
                logger.info(f"✅ Found specialized service: {facility.get('nome')}")
                return {
                    "tipo": service_type,
                    "nome": facility.get("nome", "Servizio Specialistico"),
                    "note": (
                        f"Servizio dedicato per {area}. "
                        f"Accesso: {facility.get('tipo_accesso', 'Verificare modalità')}. "
                        f"Telefono: {facility.get('contatti', {}).get('telefono', 'N/D')}"
                    ),
                    "distance_km": None
                }
        
        return None


# ============================================================================
# LEGACY COMPATIBILITY - Keep detect_emergency_keywords
# ============================================================================

def detect_emergency_keywords(user_message: str) -> str:
    """
    Detect emergency keywords in user message (legacy function).
    
    Args:
        user_message: User's message
    
    Returns:
        "RED": Critical medical emergency
        "ORANGE": Urgent situation
        "BLACK": Psychiatric emergency
        "GREEN": No emergency detected
    """
    if not user_message:
        return "GREEN"
    
    text_lower = user_message.lower().strip()
    
    # BLACK triggers (psychiatric emergency)
    black_keywords = [
        "suicidio", "uccidermi", "togliermi la vita", "farla finita",
        "ammazzarmi", "voglio morire", "non voglio più vivere",
        "autolesionismo", "tagliarmi", "farmi male"
    ]
    
    for keyword in black_keywords:
        if keyword in text_lower:
            logger.error(f"🚨 BLACK EMERGENCY: '{keyword}'")
            return "BLACK"
    
    # RED triggers (critical medical emergency)
    red_keywords = [
        "dolore toracico", "dolore petto", "oppressione torace",
        "non riesco respirare", "non riesco a respirare", "soffoco",
        "perdita di coscienza", "svenuto", "svenimento",
        "convulsioni", "crisi convulsiva",
        "emorragia massiva", "sangue abbondante",
        "paralisi", "metà corpo bloccata"
    ]
    
    for keyword in red_keywords:
        if keyword in text_lower:
            logger.error(f"🚨 RED EMERGENCY: '{keyword}'")
            return "RED"
    
    # ORANGE triggers (urgent)
    orange_keywords = [
        "dolore addominale acuto", "dolore pancia molto forte",
        "trauma cranico", "battuto forte testa",
        "febbre alta", "febbre 39", "febbre 40",
        "vomito continuo", "vomito sangue",
        "dolore insopportabile", "dolore lancinante"
    ]
    
    for keyword in orange_keywords:
        if keyword in text_lower:
            logger.warning(f"⚠️ ORANGE EMERGENCY: '{keyword}'")
            return "ORANGE"
    
    return "GREEN"


# ============================================================================
# INFORMATIONAL QUERY HANDLER
# ============================================================================

def answer_info_query(query: str, kb_path: str = "master_kb.json") -> str:
    """
    Answer informational queries by interrogating master_kb.json.
    Handles queries about hours, locations, services, contact info.
    
    Args:
        query: User's informational query
        kb_path: Path to knowledge base
    
    Returns:
        str: Answer to the query or error message
    
    Example:
        >>> answer = answer_info_query("orari farmacie Bologna")
        >>> "Farmacie a Bologna: ..." in answer
        True
    """
    query_lower = query.lower().strip()
    logger.info(f"📋 Handling INFO query: '{query}'")
    
    # Load knowledge base
    try:
        with open(kb_path, 'r', encoding='utf-8') as f:
            kb = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load KB: {e}")
        return "Mi dispiace, non riesco ad accedere alle informazioni in questo momento."
    
    facilities = kb.get("facilities", [])
    
    # === QUERY TYPE DETECTION ===
    
    # Pharmacy queries
    if "farmaci" in query_lower or "farmacia" in query_lower:
        pharmacies = [f for f in facilities if f.get("tipologia") == "Farmacia"]
        
        if not pharmacies:
            return "Non ho informazioni sulle farmacie al momento."
        
        # Extract location if present
        location_match = None
        for facility in pharmacies:
            comune = facility.get("comune", "").lower()
            if comune and comune in query_lower:
                location_match = comune
                break
        
        if location_match:
            # Filter by location
            local_pharmacies = [f for f in pharmacies if f.get("comune", "").lower() == location_match]
            
            if not local_pharmacies:
                return f"Non ho trovato farmacie a {location_match.title()}."
            
            response = f"**Farmacie a {location_match.title()}:**\n\n"
            for i, pharm in enumerate(local_pharmacies[:5], 1):  # Max 5 results
                name = pharm.get("nome", "N/D")
                address = pharm.get("indirizzo", "N/D")
                phone = pharm.get("telefono", "N/D")
                hours = pharm.get("orari", "N/D")
                
                response += f"{i}. **{name}**\n"
                response += f"   - Indirizzo: {address}\n"
                response += f"   - Telefono: {phone}\n"
                if hours and hours != "N/D":
                    response += f"   - Orari: {hours}\n"
                response += "\n"
            
            return response
        else:
            # General pharmacy info
            return f"Ho informazioni su {len(pharmacies)} farmacie. Per favore specifica il comune (es. Bologna, Modena, etc.)."
    
    # Emergency department queries
    if "pronto soccorso" in query_lower or "ps" in query_lower:
        ps_list = [f for f in facilities if f.get("tipologia") == "Pronto Soccorso"]
        
        if not ps_list:
            return "Non ho informazioni sui Pronto Soccorso al momento."
        
        response = f"**Pronto Soccorso in Emilia-Romagna** ({len(ps_list)} strutture):\n\n"
        for i, ps in enumerate(ps_list[:10], 1):
            name = ps.get("nome", "N/D")
            comune = ps.get("comune", "N/D")
            response += f"{i}. {name} - {comune}\n"
        
        response += "\n💡 Per emergenze, chiama sempre il **118**."
        return response
    
    # CAU queries
    if "cau" in query_lower or "continuità assistenziale" in query_lower:
        cau_list = [f for f in facilities if f.get("tipologia") == "CAU"]
        
        if not cau_list:
            return "Non ho informazioni sui CAU (Centri di Assistenza e Urgenza) al momento."
        
        response = f"**CAU - Centri di Assistenza e Urgenza** ({len(cau_list)} strutture):\n\n"
        for i, cau in enumerate(cau_list[:10], 1):
            name = cau.get("nome", "N/D")
            comune = cau.get("comune", "N/D")
            phone = cau.get("telefono", "N/D")
            response += f"{i}. {name} - {comune}"
            if phone and phone != "N/D":
                response += f" (Tel: {phone})"
            response += "\n"
        
        return response
    
    # Hours query
    if "orari" in query_lower or "orario" in query_lower or "aperto" in query_lower:
        return (
            "Gli orari variano per tipo di struttura:\n\n"
            "- **Pronto Soccorso**: H24, 7 giorni su 7\n"
            "- **CAU**: Solitamente 8:00-20:00, alcuni H24\n"
            "- **Farmacie**: Variabile, alcune con turni notturni\n"
            "- **MMG**: Solitamente su appuntamento, orari variabili\n\n"
            "Per informazioni specifiche, indica la struttura e il comune."
        )
    
    # Contact/phone queries
    if "telefono" in query_lower or "numero" in query_lower or "contatto" in query_lower:
        return (
            "**Numeri Utili Emilia-Romagna:**\n\n"
            "- **118**: Emergenza sanitaria\n"
            "- **116117**: Guardia Medica\n"
            "- **CUP Regionale**: 800 884 888\n\n"
            "Per contatti specifici di una struttura, indica nome e comune."
        )
    
    # Default response
    return (
        "Posso aiutarti con informazioni su:\n"
        "- 🏥 Pronto Soccorso e CAU\n"
        "- 💊 Farmacie e turni\n"
        "- 📞 Numeri utili e contatti\n"
        "- 🕐 Orari delle strutture\n\n"
        "Cosa ti serve sapere?"
    )


# ============================================================================
# SLOT FILLING - Automatic Entity Extraction
# ============================================================================

def extract_slots_from_text(text: str) -> Dict[str, any]:
    """
    Automatic slot filling from user text.
    Extracts: location (comune), symptoms, age, pain scale.
    
    Args:
        text: User's message
    
    Returns:
        Dict with extracted slots (keys match TriageState fields)
    
    Example:
        >>> slots = extract_slots_from_text("Ho mal di pancia e sono a Forlì, ho 35 anni")
        >>> slots
        {'location': 'Forlì', 'symptoms': ['mal di pancia'], 'age': 35}
    """
    text_lower = text.lower().strip()
    extracted = {}
    
    logger.info(f"🔍 Extracting slots from: '{text}'")
    
    # === EXTRACT LOCATION (Comuni Emilia-Romagna) ===
    comuni_er = [
        "bologna", "modena", "parma", "reggio emilia", "piacenza", "ferrara",
        "ravenna", "forlì", "cesena", "rimini", "imola", "faenza", "carpi",
        "sassuolo", "formigliola", "fidenza", "scandiano", "lugo", "cesenatico",
        "riccione", "cattolica", "cervia", "bellaria", "santarcangelo",
        "castelvetro", "vignola", "mirandola", "cento"
    ]
    
    for comune in comuni_er:
        if comune in text_lower:
            extracted['location'] = comune.title()
            logger.info(f"📍 Location extracted: {comune.title()}")
            break
    
    # === EXTRACT AGE ===
    age_patterns = [
        r"ho\s+(\d{1,3})\s+anni",
        r"(\d{1,3})\s+anni",
        r"età\s+(\d{1,3})",
        r"sono\s+un\w*\s+di\s+(\d{1,3})"
    ]
    
    for pattern in age_patterns:
        match = re.search(pattern, text_lower)
        if match:
            age = int(match.group(1))
            if 0 < age < 120:  # Sanity check
                extracted['age'] = age
                logger.info(f"👤 Age extracted: {age}")
                break
    
    # === EXTRACT PAIN SCALE ===
    pain_patterns = [
        r"dolore\s+(\d{1,2})\s*/?\s*10",
        r"intensità\s+(\d{1,2})",
        r"scala\s+(\d{1,2})"
    ]
    
    for pattern in pain_patterns:
        match = re.search(pattern, text_lower)
        if match:
            pain_scale = int(match.group(1))
            if 1 <= pain_scale <= 10:
                extracted['pain_scale'] = pain_scale
                logger.info(f"📊 Pain scale extracted: {pain_scale}/10")
                break
    
    # === EXTRACT SYMPTOMS ===
    symptom_keywords = [
        "dolore", "male", "febbre", "tosse", "nausea", "vomito", "diarrea",
        "vertigini", "sanguinamento", "gonfiore", "prurito", "bruciore",
        "respiro difficile", "affanno", "palpitazioni", "cefalea", "emicrania",
        "mal di testa", "mal di pancia", "mal di stomaco", "mal di schiena"
    ]
    
    detected_symptoms = []
    for symptom in symptom_keywords:
        if symptom in text_lower:
            detected_symptoms.append(symptom)
    
    if detected_symptoms:
        extracted['symptoms'] = detected_symptoms
        logger.info(f"🩺 Symptoms extracted: {', '.join(detected_symptoms)}")
    
    # === EXTRACT CHIEF COMPLAINT (first sentence) ===
    sentences = text.split('.')
    if sentences and len(sentences[0]) > 10:
        extracted['chief_complaint'] = sentences[0].strip()
        logger.info(f"📝 Chief complaint extracted: {sentences[0][:50]}...")
    
    logger.info(f"✅ Extracted {len(extracted)} slots: {list(extracted.keys())}")
    return extracted


# ============================================================================
# SINGLE QUESTION POLICY ENFORCER
# ============================================================================

def enforce_single_question(ai_response: str) -> str:
    """
    Ensure AI response contains ONLY ONE question mark.
    If multiple questions detected, keep only the first one.
    
    Args:
        ai_response: AI generated response
    
    Returns:
        str: Response with single question (if any)
    
    Example:
        >>> response = "Dove ti trovi? Hai febbre? Quanto fa male?"
        >>> enforce_single_question(response)
        "Dove ti trovi?"
    """
    if not ai_response or '?' not in ai_response:
        return ai_response
    
    # Split by question marks
    parts = ai_response.split('?')
    
    # Count actual questions (ignore empty parts)
    questions = [p.strip() for p in parts if p.strip()]
    
    if len(questions) <= 1:
        # Already single question or statement
        return ai_response
    
    # Multiple questions detected - keep first
    first_question = questions[0] + '?'
    
    logger.warning(f"⚠️ Multiple questions detected. Enforcing single question policy.")
    logger.info(f"Original: '{ai_response[:100]}...'")
    logger.info(f"Enforced: '{first_question}'")
    
    return first_question