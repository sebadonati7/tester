# models.py - Comprehensive Data Models for FSM-based Triage System
"""
Complete data models for Clinical Decision Support System (CDSS).
Implements Finite State Machine (FSM) logic with Path A/B/C differentiation.
"""

from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List, Dict, Any
from enum import Enum
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# ============================================================================
# ENUMS - Single Source of Truth
# ============================================================================

class TriagePath(str, Enum):
    """
    Percorsi differenziati di triage basati su urgenza e tipo di problema.
    
    - A: Emergenza Fisica (max 3 domande, routing rapido)
    - B: Salute Mentale (richiede consenso, routing CSM/NPIA/Consultori)
    - C: Standard (protocollo completo, routing gerarchico)
    """
    A = "EMERGENZA_FISICA"
    B = "SALUTE_MENTALE"
    C = "STANDARD"


class TriagePhase(str, Enum):
    """
    Fasi del protocollo triage in ordine sequenziale.
    
    La FSM transita attraverso queste fasi in base al path assegnato.
    """
    INTENT_DETECTION = "INTENT_DETECTION"      # Classificazione iniziale
    LOCATION = "LOCATION"                       # Comune Emilia-Romagna
    CHIEF_COMPLAINT = "CHIEF_COMPLAINT"         # Sintomo principale
    PAIN_ASSESSMENT = "PAIN_ASSESSMENT"         # Scala dolore 0-10
    RED_FLAGS = "RED_FLAGS"                     # Sintomi critici
    DEMOGRAPHICS = "DEMOGRAPHICS"               # Età, sesso, gravidanza
    ANAMNESIS = "ANAMNESIS"                     # Farmaci, allergie, condizioni croniche
    DISPOSITION = "DISPOSITION"                 # Raccomandazione finale
    EMERGENCY_OVERRIDE = "EMERGENCY_OVERRIDE"   # Override per 118 immediato


class TriageBranch(str, Enum):
    """
    Branch principale di classificazione del dialogo.
    
    - TRIAGE: Richiede slot filling e raccomandazione clinica
    - INFORMAZIONI: Risposta diretta senza dati clinici
    """
    TRIAGE = "TRIAGE"
    INFORMAZIONI = "INFORMAZIONI"


class QuestionType(str, Enum):
    """Tipologia di domanda presentata all'utente."""
    SURVEY = "survey"              # Scelta multipla (A/B/C)
    SCALE = "scale"                # Scala numerica (1-10)
    TEXT = "text"                  # Risposta libera
    INFO_REQUEST = "info_request"  # Richiesta informativa (non-triage)
    CONFIRMATION = "confirmation"  # Conferma dati


class DispositionType(str, Enum):
    """Tipo di struttura sanitaria consigliata."""
    PS = "Pronto Soccorso"
    CAU = "CAU"
    MMG = "Medico di Medicina Generale"
    CALL_118 = "118"
    CSM = "Centro Salute Mentale"
    NPIA = "Neuropsichiatria Infantile"
    CONSULTORIO = "Consultorio"
    TELEMEDICINA = "Telemedicina"


# ============================================================================
# NESTED MODELS
# ============================================================================

class PatientInfo(BaseModel):
    """
    Informazioni anagrafiche e territoriali del paziente.
    
    Validazioni:
    - location: deve essere comune Emilia-Romagna
    - age: 0-120 anni
    - sex: M/F/Altro
    """
    age: Optional[int] = Field(None, ge=0, le=120, description="Età del paziente")
    sex: Optional[str] = Field(None, description="Sesso: M, F, Altro")
    location: Optional[str] = Field(None, description="Comune Emilia-Romagna")
    pregnant: Optional[bool] = Field(None, description="Stato di gravidanza (se applicabile)")
    
    @field_validator("location")
    @classmethod
    def validate_location(cls, v: Optional[str]) -> Optional[str]:
        """Valida che il comune sia in Emilia-Romagna."""
        if v is None:
            return None
        
        # Lista comuni validati Emilia-Romagna
        COMUNI_ER = {
            "bologna", "modena", "parma", "reggio emilia", "ferrara", "ravenna",
            "rimini", "forlì", "forli", "cesena", "piacenza", "imola", "faenza",
            "carpi", "sassuolo", "formigine", "casalecchio", "san lazzaro",
            "medicina", "budrio", "lugo", "cervia", "riccione", "cattolica",
            "bellaria", "comacchio", "argenta", "cento"
        }
        
        location_lower = v.lower().strip()
        if location_lower not in COMUNI_ER:
            logger.warning(f"Location '{v}' non trovata in lista comuni ER")
            # Non sollevo eccezione per permettere fuzzy matching successivo
        
        return v
    
    @field_validator("sex")
    @classmethod
    def validate_sex(cls, v: Optional[str]) -> Optional[str]:
        """Normalizza il sesso."""
        if v is None:
            return None
        sex_upper = v.upper()
        if sex_upper in ["M", "F", "MASCHIO", "FEMMINA", "MALE", "FEMALE"]:
            return "M" if sex_upper in ["M", "MASCHIO", "MALE"] else "F"
        return "Altro"


class ClinicalData(BaseModel):
    """
    Dati clinici raccolti durante il triage.
    
    Campi:
    - chief_complaint: Sintomo principale
    - pain_scale: 0-10 (0=nessun dolore, 10=insopportabile)
    - duration: Durata sintomi (testo libero)
    - red_flags: Lista sintomi critici rilevati
    - medications: Farmaci assunti
    - allergies: Allergie note
    - chronic_conditions: Patologie croniche
    """
    chief_complaint: Optional[str] = Field(None, description="Sintomo principale")
    pain_scale: Optional[int] = Field(None, ge=0, le=10, description="Scala dolore 0-10")
    duration: Optional[str] = Field(None, description="Durata sintomi (es. '2 giorni')")
    red_flags: List[str] = Field(default_factory=list, description="Sintomi critici")
    medications: Optional[str] = Field(None, description="Farmaci in uso")
    allergies: Optional[str] = Field(None, description="Allergie")
    chronic_conditions: Optional[str] = Field(None, description="Condizioni croniche")
    
    @field_validator("red_flags")
    @classmethod
    def validate_red_flags(cls, v: List[str]) -> List[str]:
        """Normalizza e deduplica red flags."""
        if not v:
            return []
        # Rimuovi duplicati e converti in lowercase per confronto
        unique_flags = list(set([flag.strip() for flag in v if flag and flag.strip()]))
        return unique_flags


class TriageMetadata(BaseModel):
    """
    Metadati di urgenza e affidabilità della valutazione.
    
    Campi:
    - urgenza: 1 (bassa) - 5 (critica)
    - area: Area clinica (Medica, Chirurgica, Traumatologica, etc.)
    - confidence: 0.0-1.0 (affidabilità classificazione)
    - fallback_used: True se usato fallback invece di AI
    """
    urgenza: int = Field(default=3, ge=1, le=5, description="Livello urgenza 1-5")
    area: str = Field(default="Generale", description="Area clinica")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="Affidabilità")
    fallback_used: bool = Field(default=False, description="Fallback attivato")
    
    @field_validator("urgenza", mode="before")
    @classmethod
    def normalize_urgenza(cls, v: Any) -> int:
        """
        Normalizza urgenza: converte 0 o valori negativi in 1 (bassa urgenza).
        Clamp in range 1-5 per evitare crash di validazione.
        """
        try:
            val = int(v)
            # Se è 0 o negativo, forzalo a 1 (Bassa urgenza) invece di crashare
            return max(1, min(5, val))
        except (ValueError, TypeError):
            # Default safe se conversione fallisce
            return 1


class DispositionRecommendation(BaseModel):
    """
    Raccomandazione finale con struttura sanitaria consigliata.
    
    Generata nella fase DISPOSITION dopo completamento slot filling.
    """
    type: DispositionType = Field(..., description="Tipo struttura")
    urgency: int = Field(..., ge=1, le=5, description="Urgenza 1-5")
    facility_name: str = Field(..., description="Nome struttura specifica")
    note: Optional[str] = Field(None, description="Note aggiuntive")
    distance_km: Optional[float] = Field(None, description="Distanza in km")
    sbar_summary: Optional[str] = Field(None, description="Report SBAR strutturato")


# ============================================================================
# MAIN STATE MODEL - FSM Core
# ============================================================================

class TriageState(BaseModel):
    """
    Stato principale del triage - Single Source of Truth per FSM.
    
    Gestisce:
    - Transizioni di fase
    - Validazione completezza dati
    - Generazione SBAR
    - Routing Path A/B/C
    
    Attributes:
        session_id: ID univoco sessione
        timestamp_start: Inizio sessione
        current_phase: Fase corrente FSM
        assigned_path: Path A/B/C assegnato
        assigned_branch: TRIAGE o INFORMAZIONI
        question_count: Contatore domande (limite Path A = 3)
        patient_info: Dati anagrafici
        clinical_data: Dati clinici
        disposition: Raccomandazione finale
        metadata: Metadati urgenza
        consent_given: Consenso Privacy (Path B)
    """
    session_id: str = Field(..., description="ID sessione univoco")
    timestamp_start: datetime = Field(default_factory=datetime.now, description="Inizio sessione")
    
    # FSM State
    current_phase: TriagePhase = Field(default=TriagePhase.INTENT_DETECTION)
    assigned_path: Optional[TriagePath] = Field(None, description="Path A/B/C")
    assigned_branch: Optional[TriageBranch] = Field(None, description="TRIAGE o INFORMAZIONI")
    question_count: int = Field(default=0, description="Contatore domande (Path A max 3)")
    
    # Clinical Data
    patient_info: PatientInfo = Field(default_factory=PatientInfo)
    clinical_data: ClinicalData = Field(default_factory=ClinicalData)
    disposition: Optional[DispositionRecommendation] = None
    metadata: TriageMetadata = Field(default_factory=TriageMetadata)
    
    # Path B Specific
    consent_given: bool = Field(default=False, description="Consenso Privacy (Path B)")
    
    # ========================================================================
    # CRITICAL METHODS
    # ========================================================================
    
    def get_completion_percentage(self) -> float:
        """
        Calcola percentuale completamento slot obbligatori.
        
        Slot obbligatori variano per Path:
        - Path A: LOCATION, CHIEF_COMPLAINT, RED_FLAGS (3 slot)
        - Path B: LOCATION, DEMOGRAPHICS, CHIEF_COMPLAINT, consenso (4 slot)
        - Path C: Tutti (7 slot)
        
        Returns:
            Percentuale 0.0-100.0
        """
        if self.assigned_path == TriagePath.A:
            required_slots = 3
            filled = sum([
                1 if self.patient_info.location else 0,
                1 if self.clinical_data.chief_complaint else 0,
                1 if self.clinical_data.red_flags else 0
            ])
        elif self.assigned_path == TriagePath.B:
            required_slots = 4
            filled = sum([
                1 if self.patient_info.location else 0,
                1 if self.patient_info.age else 0,
                1 if self.clinical_data.chief_complaint else 0,
                1 if self.consent_given else 0
            ])
        else:  # Path C
            required_slots = 7
            filled = sum([
                1 if self.patient_info.location else 0,
                1 if self.clinical_data.chief_complaint else 0,
                1 if self.clinical_data.pain_scale is not None else 0,
                1 if self.clinical_data.red_flags else 0,
                1 if self.patient_info.age else 0,
                1 if self.patient_info.sex else 0,
                1 if self.clinical_data.medications else 0
            ])
        
        return (filled / required_slots) * 100.0 if required_slots > 0 else 0.0
    
    def get_missing_critical_slots(self) -> List[str]:
        """
        Ritorna slot mancanti ordinati per priorità.
        
        Priorità Path A:
        1. LOCATION (territoriale)
        2. CHIEF_COMPLAINT
        3. RED_FLAGS
        
        Priorità Path B:
        1. Consenso
        2. LOCATION
        3. DEMOGRAPHICS (età per CSM/NPIA)
        4. CHIEF_COMPLAINT
        
        Priorità Path C:
        1. LOCATION
        2. CHIEF_COMPLAINT
        3. PAIN_SCALE
        4. RED_FLAGS
        5. DEMOGRAPHICS
        6. ANAMNESIS
        
        Returns:
            Lista slot mancanti in ordine priorità
        """
        missing = []
        
        if self.assigned_path == TriagePath.A:
            if not self.patient_info.location:
                missing.append("LOCATION")
            if not self.clinical_data.chief_complaint:
                missing.append("CHIEF_COMPLAINT")
            if not self.clinical_data.red_flags:
                missing.append("RED_FLAGS")
                
        elif self.assigned_path == TriagePath.B:
            if not self.consent_given:
                missing.append("CONSENT")
            if not self.patient_info.location:
                missing.append("LOCATION")
            if not self.patient_info.age:
                missing.append("DEMOGRAPHICS")
            if not self.clinical_data.chief_complaint:
                missing.append("CHIEF_COMPLAINT")
                
        else:  # Path C
            if not self.patient_info.location:
                missing.append("LOCATION")
            if not self.clinical_data.chief_complaint:
                missing.append("CHIEF_COMPLAINT")
            if self.clinical_data.pain_scale is None:
                missing.append("PAIN_SCALE")
            if not self.clinical_data.red_flags:
                missing.append("RED_FLAGS")
            if not self.patient_info.age:
                missing.append("DEMOGRAPHICS")
            if not self.clinical_data.medications:
                missing.append("ANAMNESIS")
        
        return missing
    
    def can_transition_to_disposition(self) -> bool:
        """
        Valida se ci sono dati sufficienti per generare SBAR.
        
        Criteri minimi per Path:
        - A: LOCATION + CHIEF_COMPLAINT + RED_FLAGS
        - B: LOCATION + AGE + CHIEF_COMPLAINT + CONSENT
        - C: Tutti slot obbligatori
        
        Returns:
            True se può procedere a DISPOSITION
        """
        missing = self.get_missing_critical_slots()
        
        # Path A può procedere anche con dati minimi per rapidità
        if self.assigned_path == TriagePath.A:
            return len(missing) == 0 or self.question_count >= 3
        
        # Path B e C richiedono completamento
        return len(missing) == 0
    
    def to_sbar_summary(self) -> str:
        """
        Genera report SBAR (Situation, Background, Assessment, Recommendation).
        
        Format SBAR Standard:
        - S (Situation): Sintomo principale + intensità
        - B (Background): Età, sesso, anamnesi rilevante
        - A (Assessment): Red flags, urgenza assegnata
        - R (Recommendation): Struttura consigliata
        
        Returns:
            Stringa formattata SBAR
        """
        # Situation
        complaint = self.clinical_data.chief_complaint or "Non specificato"
        pain = f"Dolore {self.clinical_data.pain_scale}/10" if self.clinical_data.pain_scale is not None else "Dolore non valutato"
        situation = f"SITUAZIONE: {complaint}. {pain}."
        
        # Background
        age_str = f"{self.patient_info.age} anni" if self.patient_info.age else "Età non specificata"
        sex_str = self.patient_info.sex or "Sesso non specificato"
        location_str = self.patient_info.location or "Località non specificata"
        meds = f"Farmaci: {self.clinical_data.medications}" if self.clinical_data.medications else ""
        background = f"BACKGROUND: {age_str}, {sex_str}, {location_str}. {meds}"
        
        # Assessment
        red_flags_str = ", ".join(self.clinical_data.red_flags) if self.clinical_data.red_flags else "Nessun red flag rilevato"
        urgency_str = f"Urgenza {self.metadata.urgenza}/5"
        assessment = f"VALUTAZIONE: {red_flags_str}. {urgency_str}. Area: {self.metadata.area}."
        
        # Recommendation
        if self.disposition:
            recommendation = f"RACCOMANDAZIONE: {self.disposition.facility_name} ({self.disposition.type}). {self.disposition.note or ''}"
        else:
            recommendation = "RACCOMANDAZIONE: In attesa di completamento valutazione."
        
        return f"{situation}\n{background}\n{assessment}\n{recommendation}"
    
    def has_critical_red_flags(self) -> bool:
        """
        Verifica presenza red flags che richiedono 118 immediato.
        
        Red Flags Critici:
        - Dolore toracico
        - Dispnea grave
        - Perdita coscienza
        - Convulsioni
        - Emorragia massiva
        - Paralisi
        
        Returns:
            True se presente almeno un red flag critico
        """
        CRITICAL_FLAGS = [
            "dolore toracico",
            "dispnea grave",
            "perdita coscienza",
            "convulsioni",
            "emorragia massiva",
            "paralisi"
        ]
        
        if not self.clinical_data.red_flags:
            return False
        
        red_flags_lower = [rf.lower() for rf in self.clinical_data.red_flags]
        
        for critical_flag in CRITICAL_FLAGS:
            if any(critical_flag in rf for rf in red_flags_lower):
                logger.critical(f"🚨 Critical red flag detected: {critical_flag}")
                return True
        
        return False


# ============================================================================
# LEGACY COMPATIBILITY - TriageResponse for API
# ============================================================================

class SBARReport(BaseModel):
    """Report SBAR strutturato (legacy compatibility)."""
    situation: str = Field(..., description="Sintomo principale e intensità")
    background: Dict[str, Any] = Field(default_factory=dict, description="Età, sesso, farmaci, etc.")
    assessment: List[str] = Field(default_factory=list, description="Risposte chiave")
    recommendation: str = Field(..., description="Struttura suggerita")


class TriageResponse(BaseModel):
    """
    Schema risposta AI per compatibilità con frontend.
    
    Mantiene retrocompatibilità con sistema esistente mentre
    permette transizione a TriageState internamente.
    """
    testo: str = Field(..., max_length=1000, description="Messaggio per utente")
    tipo_domanda: QuestionType = Field(..., description="Tipo domanda")
    opzioni: Optional[List[str]] = Field(None, description="Opzioni per survey")
    fase_corrente: str = Field(..., description="Fase protocollo corrente")
    dati_estratti: Dict[str, Any] = Field(default_factory=dict, description="Dati estratti")
    metadata: TriageMetadata = Field(..., description="Metadati urgenza")
    sbar: Optional[SBARReport] = Field(None, description="Report SBAR se DISPOSITION")
    
    @field_validator("opzioni")
    @classmethod
    def validate_options(cls, v: Any, info: Any) -> Any:
        """Valida opzioni per domande survey."""
        if info.data.get("tipo_domanda") == QuestionType.SURVEY:
            if not v or len(v) < 2:
                return ["Sì", "No", "Non so"]
        return v