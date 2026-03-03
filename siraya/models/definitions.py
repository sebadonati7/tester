"""
SIRAYA Health Navigator - Data Models
V1.0: Il "Vocabolario" - Single source of truth for data structures.

This module defines:
- Enums for triage states and types
- Pydantic models for data validation
- Type definitions for the entire application
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any
from enum import Enum
from datetime import datetime


# ============================================================================
# ENUMS - State Definitions
# ============================================================================

class PageName(str, Enum):
    """Available pages in the application."""
    LANDING = "LANDING"
    CHAT = "CHAT"
    MAP = "MAP"
    REPORT = "REPORT"
    DASHBOARD = "DASHBOARD"
    ADMIN = "ADMIN"


class TriagePath(str, Enum):
    """
    Triage paths based on urgency and problem type.
    
    - A: Physical Emergency (max 3 questions, fast routing)
    - B: Mental Health (requires consent, CSM/NPIA routing)
    - C: Standard (full protocol, hierarchical routing)
    """
    A = "EMERGENZA_FISICA"
    B = "SALUTE_MENTALE"
    C = "STANDARD"


class TriagePhase(str, Enum):
    """Phases of the triage protocol in sequence."""
    INTENT_DETECTION = "INTENT_DETECTION"
    LOCATION = "LOCATION"
    CHIEF_COMPLAINT = "CHIEF_COMPLAINT"
    PAIN_ASSESSMENT = "PAIN_ASSESSMENT"
    RED_FLAGS = "RED_FLAGS"
    DEMOGRAPHICS = "DEMOGRAPHICS"
    ANAMNESIS = "ANAMNESIS"
    DISPOSITION = "DISPOSITION"
    EMERGENCY_OVERRIDE = "EMERGENCY_OVERRIDE"


class TriageBranch(str, Enum):
    """Main dialogue classification branch."""
    TRIAGE = "TRIAGE"
    INFORMAZIONI = "INFORMAZIONI"


class QuestionType(str, Enum):
    """Type of question presented to user."""
    SURVEY = "survey"           # Multiple choice (A/B/C)
    SCALE = "scale"             # Numeric scale (1-10)
    TEXT = "text"               # Free text
    INFO_REQUEST = "info_request"
    CONFIRMATION = "confirmation"


class DispositionType(str, Enum):
    """Recommended healthcare facility type."""
    PS = "Pronto Soccorso"
    CAU = "CAU"
    MMG = "Medico di Medicina Generale"
    CALL_118 = "118"
    CSM = "Centro Salute Mentale"
    NPIA = "Neuropsichiatria Infantile"
    CONSULTORIO = "Consultorio"
    TELEMEDICINA = "Telemedicina"


class UrgencyLevel(int, Enum):
    """Urgency levels (1-5 scale)."""
    BASSA = 1       # White code
    LIEVE = 2       # Green code
    MEDIA = 3       # Yellow code
    ALTA = 4        # Orange code
    CRITICA = 5     # Red code


# ============================================================================
# NESTED MODELS
# ============================================================================

class PatientInfo(BaseModel):
    """Patient demographic and location information."""
    age: Optional[int] = Field(None, ge=0, le=120, description="Patient age")
    sex: Optional[str] = Field(None, description="Sex: M, F, Other")
    location: Optional[str] = Field(None, description="Municipality (Emilia-Romagna)")
    pregnant: Optional[bool] = Field(None, description="Pregnancy status")
    
    @field_validator("sex")
    @classmethod
    def normalize_sex(cls, v: Optional[str]) -> Optional[str]:
        """Normalize sex value."""
        if v is None:
            return None
        sex_upper = v.upper()
        if sex_upper in ["M", "MASCHIO", "MALE"]:
            return "M"
        if sex_upper in ["F", "FEMMINA", "FEMALE"]:
            return "F"
        return "Altro"


class ClinicalData(BaseModel):
    """Clinical data collected during triage."""
    chief_complaint: Optional[str] = Field(None, description="Main symptom")
    pain_scale: Optional[int] = Field(None, ge=0, le=10, description="Pain scale 0-10")
    duration: Optional[str] = Field(None, description="Symptom duration")
    red_flags: List[str] = Field(default_factory=list, description="Critical symptoms")
    medications: Optional[str] = Field(None, description="Current medications")
    allergies: Optional[str] = Field(None, description="Known allergies")
    chronic_conditions: Optional[str] = Field(None, description="Chronic conditions")
    
    @field_validator("red_flags")
    @classmethod
    def dedupe_red_flags(cls, v: List[str]) -> List[str]:
        """Remove duplicates from red flags."""
        if not v:
            return []
        return list(set([f.strip() for f in v if f and f.strip()]))


class TriageMetadata(BaseModel):
    """Urgency and reliability metadata."""
    urgenza: int = Field(default=3, ge=1, le=5, description="Urgency level 1-5")
    area: str = Field(default="Generale", description="Clinical area")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="Classification reliability")
    fallback_used: bool = Field(default=False, description="Fallback activated")
    
    @field_validator("urgenza", mode="before")
    @classmethod
    def clamp_urgenza(cls, v: Any) -> int:
        """Clamp urgency to valid range."""
        try:
            val = int(v)
            return max(1, min(5, val))
        except (ValueError, TypeError):
            return 3


class DispositionRecommendation(BaseModel):
    """Final recommendation with healthcare facility."""
    type: DispositionType = Field(..., description="Facility type")
    urgency: int = Field(..., ge=1, le=5, description="Urgency 1-5")
    facility_name: str = Field(..., description="Specific facility name")
    note: Optional[str] = Field(None, description="Additional notes")
    distance_km: Optional[float] = Field(None, description="Distance in km")
    sbar_summary: Optional[str] = Field(None, description="SBAR report")


# ============================================================================
# MAIN STATE MODEL
# ============================================================================

class TriageState(BaseModel):
    """
    Main triage state - Single Source of Truth for FSM.
    
    Manages:
    - Phase transitions
    - Data completeness validation
    - SBAR generation
    - Path A/B/C routing
    """
    session_id: str = Field(..., description="Unique session ID")
    timestamp_start: datetime = Field(default_factory=datetime.now)
    
    # FSM State
    current_phase: TriagePhase = Field(default=TriagePhase.INTENT_DETECTION)
    assigned_path: Optional[TriagePath] = Field(None)
    assigned_branch: Optional[TriageBranch] = Field(None)
    question_count: int = Field(default=0)
    
    # Clinical Data
    patient_info: PatientInfo = Field(default_factory=PatientInfo)
    clinical_data: ClinicalData = Field(default_factory=ClinicalData)
    disposition: Optional[DispositionRecommendation] = None
    metadata: TriageMetadata = Field(default_factory=TriageMetadata)
    
    # Path B Specific
    consent_given: bool = Field(default=False)
    
    def get_completion_percentage(self) -> float:
        """Calculate completion percentage based on path."""
        if self.assigned_path == TriagePath.A:
            required = 3
            filled = sum([
                1 if self.patient_info.location else 0,
                1 if self.clinical_data.chief_complaint else 0,
                1 if self.clinical_data.red_flags else 0
            ])
        elif self.assigned_path == TriagePath.B:
            required = 4
            filled = sum([
                1 if self.patient_info.location else 0,
                1 if self.patient_info.age else 0,
                1 if self.clinical_data.chief_complaint else 0,
                1 if self.consent_given else 0
            ])
        else:
            required = 7
            filled = sum([
                1 if self.patient_info.location else 0,
                1 if self.clinical_data.chief_complaint else 0,
                1 if self.clinical_data.pain_scale is not None else 0,
                1 if self.clinical_data.red_flags else 0,
                1 if self.patient_info.age else 0,
                1 if self.patient_info.sex else 0,
                1 if self.clinical_data.medications else 0
            ])
        
        return (filled / required * 100.0) if required > 0 else 0.0
    
    def to_sbar_summary(self) -> str:
        """Generate SBAR report string."""
        # Situation
        complaint = self.clinical_data.chief_complaint or "Non specificato"
        pain = f"Dolore {self.clinical_data.pain_scale}/10" if self.clinical_data.pain_scale is not None else ""
        situation = f"SITUAZIONE: {complaint}. {pain}"
        
        # Background
        age = f"{self.patient_info.age} anni" if self.patient_info.age else "Età N/D"
        sex = self.patient_info.sex or "Sesso N/D"
        location = self.patient_info.location or "Località N/D"
        background = f"BACKGROUND: {age}, {sex}, {location}"
        
        # Assessment
        flags = ", ".join(self.clinical_data.red_flags) if self.clinical_data.red_flags else "Nessun red flag"
        assessment = f"VALUTAZIONE: {flags}. Urgenza {self.metadata.urgenza}/5"
        
        # Recommendation
        if self.disposition:
            recommendation = f"RACCOMANDAZIONE: {self.disposition.facility_name}"
        else:
            recommendation = "RACCOMANDAZIONE: In attesa"
        
        return f"{situation}\n{background}\n{assessment}\n{recommendation}"


# ============================================================================
# API RESPONSE MODEL
# ============================================================================

class TriageResponse(BaseModel):
    """Response schema from AI for frontend compatibility."""
    testo: str = Field(..., max_length=2000, description="Message for user")
    tipo_domanda: QuestionType = Field(..., description="Question type")
    opzioni: Optional[List[str]] = Field(None, description="Options for survey")
    fase_corrente: str = Field(..., description="Current protocol phase")
    dati_estratti: Dict[str, Any] = Field(default_factory=dict, description="Extracted data")
    metadata: TriageMetadata = Field(..., description="Urgency metadata")
    
    @field_validator("opzioni")
    @classmethod
    def validate_options(cls, v: Any, info: Any) -> Any:
        """Validate options for survey questions."""
        if info.data.get("tipo_domanda") == QuestionType.SURVEY:
            if not v or len(v) < 2:
                return ["Sì", "No", "Non so"]
        return v


# ============================================================================
# AGGREGATE MODEL FOR PATIENT SESSION
# ============================================================================

class PatientData(BaseModel):
    """
    Complete patient data for a session.
    
    Used for:
    - Logging to Supabase
    - Report generation
    - Analytics
    """
    session_id: str
    timestamp: datetime = Field(default_factory=datetime.now)
    
    # Patient
    age: Optional[int] = None
    sex: Optional[str] = None
    location: Optional[str] = None
    
    # Clinical
    chief_complaint: Optional[str] = None
    pain_scale: Optional[int] = None
    red_flags: List[str] = Field(default_factory=list)
    medications: Optional[str] = None
    
    # Outcome
    urgency_level: int = 3
    specialization: str = "Generale"
    disposition_type: Optional[str] = None
    facility_name: Optional[str] = None
    
    # Metadata
    triage_path: str = "C"
    question_count: int = 0
    completion_percentage: float = 0.0
    
    def to_log_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging."""
        return {
            "session_id": self.session_id,
            "timestamp": self.timestamp.isoformat(),
            "age": self.age,
            "sex": self.sex,
            "location": self.location,
            "chief_complaint": self.chief_complaint,
            "pain_scale": self.pain_scale,
            "red_flags": self.red_flags,
            "urgency_level": self.urgency_level,
            "specialization": self.specialization,
            "disposition_type": self.disposition_type,
            "facility_name": self.facility_name,
            "triage_path": self.triage_path,
        }

