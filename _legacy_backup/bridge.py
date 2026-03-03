# bridge.py - Session Management and Data Normalization
"""
Manages triage session state synchronization and entity extraction.

Features:
- Historical memory management (prevents context loss)
- Entity extraction with regex patterns
- Data normalization and validation
- Merge rules for state updates
- Legacy session data conversion

Classes:
    TriageSessionBridge: Main session management class
"""

import re
import logging
import difflib
from typing import Dict, List, Any, Optional
from pydantic import ValidationError

from models import (
    TriageState, TriagePath, TriagePhase, TriageBranch,
    PatientInfo, ClinicalData, TriageMetadata
)

logger = logging.getLogger(__name__)

# ============================================================================
# COMUNI EMILIA-ROMAGNA (for validation)
# ============================================================================

COMUNI_ER = {
    "bologna", "modena", "parma", "reggio emilia", "ferrara", "ravenna",
    "rimini", "forlì", "forli", "cesena", "piacenza", "imola", "faenza",
    "carpi", "sassuolo", "formigine", "casalecchio", "san lazzaro",
    "medicina", "budrio", "lugo", "cervia", "riccione", "cattolica",
    "bellaria", "comacchio", "argenta", "cento", "reggio nell'emilia"
}

# ============================================================================
# RED FLAGS KEYWORD MAPPING
# ============================================================================

RED_FLAGS_KEYWORDS = {
    "dolore toracico": "Dolore toracico",
    "dolore al petto": "Dolore toracico",
    "dolore petto": "Dolore toracico",
    "oppressione torace": "Dolore toracico",
    "non riesco a respirare": "Dispnea grave",
    "non riesco respirare": "Dispnea grave",
    "difficoltà respiratoria": "Dispnea",
    "soffoco": "Dispnea grave",
    "perdita di coscienza": "Perdita coscienza",
    "svenuto": "Perdita coscienza",
    "svenimento": "Perdita coscienza",
    "convulsioni": "Convulsioni",
    "crisi convulsiva": "Convulsioni",
    "emorragia": "Emorragia massiva",
    "sangue abbondante": "Emorragia massiva",
    "molto sangue": "Emorragia massiva",
    "paralisi": "Paralisi",
    "non muovo il braccio": "Paralisi",
    "non muovo la gamba": "Paralisi",
    "febbre alta": "Febbre >39°C",
    "febbre 39": "Febbre >39°C",
    "febbre 40": "Febbre >39°C",
    "trauma cranico": "Trauma cranico",
    "battuto testa": "Trauma cranico",
    "vomito persistente": "Vomito persistente",
    "vomito continuo": "Vomito persistente",
    "dolore addominale acuto": "Dolore addominale acuto",
    "dolore pancia forte": "Dolore addominale acuto"
}


# ============================================================================
# MAIN CLASS
# ============================================================================

class TriageSessionBridge:
    """
    Manages triage session state and data synchronization.
    
    Responsibilities:
    1. Merge new extracted data with existing state
    2. Extract entities from user text (regex-based)
    3. Validate data completeness
    4. Convert legacy session data to TriageState
    
    Methods:
        sync_session_context: Merge new data into existing state
        extract_entities_from_text: Extract entities with regex
        validate_triage_completeness: Check if triage is complete
        convert_legacy_session_data: Convert dict to TriageState
    """
    
    def __init__(self):
        """Initialize bridge with logging."""
        logger.info("✅ TriageSessionBridge initialized")
    
    # ========================================================================
    # 1. SYNC SESSION CONTEXT - Merge Rules
    # ========================================================================
    
    def sync_session_context(
        self,
        current_state: TriageState,
        new_extracted_data: Dict[str, Any]
    ) -> TriageState:
        """
        Merge new extracted data into existing state with strict rules.
        
        Merge Rules:
        1. Existing data is NEVER overwritten (prevent data loss)
        2. Red flags ACCUMULATE (no overwrite)
        3. Urgency can only INCREASE, never decrease
        4. Location validated against Emilia-Romagna comuni
        
        Args:
            current_state: Current TriageState object
            new_extracted_data: Dict with newly extracted data
        
        Returns:
            Updated TriageState object
        
        Field Mapping:
            - LOCATION → patient_info.location
            - age → patient_info.age
            - sex → patient_info.sex
            - CHIEF_COMPLAINT → clinical_data.chief_complaint
            - PAIN_SCALE → clinical_data.pain_scale
            - RED_FLAGS → clinical_data.red_flags (accumulate)
            - duration → clinical_data.duration
            - medications → clinical_data.medications
            - allergies → clinical_data.allergies
            - chronic_conditions → clinical_data.chronic_conditions
        """
        if not new_extracted_data:
            logger.debug("⚠️ No new data to sync")
            return current_state
        
        logger.info(f"🔄 Syncing session context with keys: {list(new_extracted_data.keys())}")
        
        # === PATIENT INFO ===
        
        # Location (with validation)
        if "LOCATION" in new_extracted_data and not current_state.patient_info.location:
            location = new_extracted_data["LOCATION"]
            # Validate against Emilia-Romagna comuni
            if isinstance(location, str):
                location_lower = location.lower().strip()
                if location_lower in COMUNI_ER:
                    current_state.patient_info.location = location
                    logger.info(f"✅ Location set: {location}")
                else:
                    # Try fuzzy matching
                    matches = difflib.get_close_matches(location_lower, COMUNI_ER, n=1, cutoff=0.8)
                    if matches:
                        current_state.patient_info.location = matches[0].title()
                        logger.info(f"✅ Location fuzzy matched: {location} → {matches[0]}")
                    else:
                        logger.warning(f"⚠️ Location '{location}' not in Emilia-Romagna")
                        # Store anyway for manual review
                        current_state.patient_info.location = location
        
        # Age (with conversion and validation)
        if "age" in new_extracted_data and current_state.patient_info.age is None:
            try:
                age = int(new_extracted_data["age"])
                if 0 <= age <= 120:
                    current_state.patient_info.age = age
                    logger.info(f"✅ Age set: {age}")
                else:
                    logger.warning(f"⚠️ Age {age} out of range 0-120")
            except (ValueError, TypeError):
                logger.warning(f"⚠️ Invalid age value: {new_extracted_data['age']}")
        
        # Sex
        if "sex" in new_extracted_data and not current_state.patient_info.sex:
            sex = new_extracted_data["sex"]
            if isinstance(sex, str):
                current_state.patient_info.sex = sex
                logger.info(f"✅ Sex set: {sex}")
        
        # Pregnant
        if "pregnant" in new_extracted_data and current_state.patient_info.pregnant is None:
            pregnant = new_extracted_data["pregnant"]
            if isinstance(pregnant, bool):
                current_state.patient_info.pregnant = pregnant
                logger.info(f"✅ Pregnant status set: {pregnant}")
        
        # === CLINICAL DATA ===
        
        # Chief Complaint
        if "CHIEF_COMPLAINT" in new_extracted_data and not current_state.clinical_data.chief_complaint:
            complaint = new_extracted_data["CHIEF_COMPLAINT"]
            if isinstance(complaint, str) and complaint.strip():
                current_state.clinical_data.chief_complaint = complaint
                logger.info(f"✅ Chief complaint set: {complaint}")
        
        # Pain Scale (with validation 0-10)
        if "PAIN_SCALE" in new_extracted_data and current_state.clinical_data.pain_scale is None:
            try:
                pain = int(new_extracted_data["PAIN_SCALE"])
                if 0 <= pain <= 10:
                    current_state.clinical_data.pain_scale = pain
                    logger.info(f"✅ Pain scale set: {pain}/10")
                else:
                    logger.warning(f"⚠️ Pain scale {pain} out of range 0-10")
            except (ValueError, TypeError):
                logger.warning(f"⚠️ Invalid pain scale value: {new_extracted_data['PAIN_SCALE']}")
        
        # Red Flags (ACCUMULATE - never overwrite)
        if "RED_FLAGS" in new_extracted_data:
            new_flags = new_extracted_data["RED_FLAGS"]
            
            # Handle both string and list
            if isinstance(new_flags, str):
                new_flags = [new_flags] if new_flags.strip() else []
            elif not isinstance(new_flags, list):
                new_flags = []
            
            # Accumulate (remove duplicates)
            existing_flags = set(current_state.clinical_data.red_flags)
            for flag in new_flags:
                if flag and flag.strip() and flag not in existing_flags:
                    current_state.clinical_data.red_flags.append(flag)
                    existing_flags.add(flag)
                    logger.info(f"✅ Red flag added: {flag}")
        
        # Duration
        if "duration" in new_extracted_data and not current_state.clinical_data.duration:
            duration = new_extracted_data["duration"]
            if isinstance(duration, str) and duration.strip():
                current_state.clinical_data.duration = duration
                logger.info(f"✅ Duration set: {duration}")
        
        # Medications
        if "medications" in new_extracted_data and not current_state.clinical_data.medications:
            medications = new_extracted_data["medications"]
            if isinstance(medications, str) and medications.strip():
                current_state.clinical_data.medications = medications
                logger.info(f"✅ Medications set: {medications}")
        
        # Allergies
        if "allergies" in new_extracted_data and not current_state.clinical_data.allergies:
            allergies = new_extracted_data["allergies"]
            if isinstance(allergies, str) and allergies.strip():
                current_state.clinical_data.allergies = allergies
                logger.info(f"✅ Allergies set: {allergies}")
        
        # Chronic Conditions
        if "chronic_conditions" in new_extracted_data and not current_state.clinical_data.chronic_conditions:
            conditions = new_extracted_data["chronic_conditions"]
            if isinstance(conditions, str) and conditions.strip():
                current_state.clinical_data.chronic_conditions = conditions
                logger.info(f"✅ Chronic conditions set: {conditions}")
        
        # === METADATA ===
        
        # Urgency (can only INCREASE)
        if "urgenza" in new_extracted_data:
            try:
                new_urgency = int(new_extracted_data["urgenza"])
                if new_urgency > current_state.metadata.urgenza:
                    old_urgency = current_state.metadata.urgenza
                    current_state.metadata.urgenza = new_urgency
                    logger.info(f"⬆️ Urgency increased: {old_urgency} → {new_urgency}")
                elif new_urgency < current_state.metadata.urgenza:
                    logger.info(f"⚠️ Urgency NOT decreased: keeping {current_state.metadata.urgenza} (tried {new_urgency})")
            except (ValueError, TypeError):
                logger.warning(f"⚠️ Invalid urgency value: {new_extracted_data['urgenza']}")
        
        return current_state
    
    # ========================================================================
    # 2. EXTRACT ENTITIES FROM TEXT - Regex-based
    # ========================================================================
    
    def extract_entities_from_text(self, user_text: str) -> Dict[str, Any]:
        """
        Extract entities from user text using regex patterns.
        
        Extracted Entities:
        - age: "ho 45 anni", "45 anni", "età 45"
        - pain_scale: "dolore 7/10", "7 su 10", "scala 7"
        - duration: "da 2 giorni", "da 3 ore", "da una settimana"
        - location: Comuni Emilia-Romagna
        - red_flags: Keyword matching from RED_FLAGS_KEYWORDS
        
        Args:
            user_text: Raw user input text
        
        Returns:
            Dict with extracted entities
        
        Example:
            >>> bridge = TriageSessionBridge()
            >>> bridge.extract_entities_from_text("Ho 35 anni e sono a Bologna")
            {'age': 35, 'LOCATION': 'Bologna'}
        """
        if not user_text or not isinstance(user_text, str):
            return {}
        
        extracted = {}
        text_lower = user_text.lower()
        
        logger.debug(f"🔍 Extracting entities from: '{user_text}'")
        
        # === AGE ===
        age_patterns = [
            r"(?:ho|età|anni)\s*(\d{1,3})\s*anni",
            r"(\d{1,3})\s*anni",
            r"età\s*(\d{1,3})"
        ]
        
        for pattern in age_patterns:
            match = re.search(pattern, text_lower)
            if match:
                try:
                    age = int(match.group(1))
                    if 0 <= age <= 120:
                        extracted["age"] = age
                        logger.debug(f"  ✅ Age extracted: {age}")
                        break
                except ValueError:
                    pass
        
        # === PAIN SCALE ===
        pain_patterns = [
            r"dolore\s*(\d{1,2})\s*/\s*10",
            r"(\d{1,2})\s*su\s*10",
            r"scala\s*(\d{1,2})",
            r"dolore\s*(\d{1,2})"
        ]
        
        for pattern in pain_patterns:
            match = re.search(pattern, text_lower)
            if match:
                try:
                    pain = int(match.group(1))
                    if 0 <= pain <= 10:
                        extracted["PAIN_SCALE"] = pain
                        logger.debug(f"  ✅ Pain scale extracted: {pain}/10")
                        break
                except ValueError:
                    pass
        
        # === DURATION ===
        duration_patterns = [
            r"da\s+(\d+)\s+(giorni?|ore?|settimane?|mesi?)",
            r"(\d+)\s+(giorni?|ore?|settimane?|mesi?)\s+fa"
        ]
        
        for pattern in duration_patterns:
            match = re.search(pattern, text_lower)
            if match:
                duration_text = match.group(0)
                extracted["duration"] = duration_text
                logger.debug(f"  ✅ Duration extracted: {duration_text}")
                break
        
        # === LOCATION (Comuni ER) ===
        for comune in COMUNI_ER:
            if comune in text_lower:
                # Capitalize properly
                extracted["LOCATION"] = comune.title()
                logger.debug(f"  ✅ Location extracted: {comune.title()}")
                break
        
        # === RED FLAGS ===
        detected_flags = []
        for keyword, flag_name in RED_FLAGS_KEYWORDS.items():
            if keyword in text_lower:
                detected_flags.append(flag_name)
                logger.debug(f"  🚨 Red flag detected: {flag_name}")
        
        if detected_flags:
            extracted["RED_FLAGS"] = detected_flags
        
        logger.info(f"🔍 Extracted {len(extracted)} entities: {list(extracted.keys())}")
        
        return extracted
    
    # ========================================================================
    # 3. VALIDATE TRIAGE COMPLETENESS
    # ========================================================================
    
    def validate_triage_completeness(self, state: TriageState) -> Dict[str, Any]:
        """
        Validate if triage data collection is complete.
        
        Checks:
        - Missing critical slots
        - Completion percentage
        - Can proceed to disposition
        - Has critical red flags
        
        Args:
            state: Current TriageState object
        
        Returns:
            Dict with validation results:
            {
                'is_complete': bool,
                'missing_slots': List[str],
                'can_proceed_disposition': bool,
                'completion_percentage': float,
                'has_critical_red_flags': bool
            }
        """
        missing_slots = state.get_missing_critical_slots()
        completion_pct = state.get_completion_percentage()
        can_proceed = state.can_transition_to_disposition()
        has_critical_flags = state.has_critical_red_flags()
        
        is_complete = len(missing_slots) == 0
        
        result = {
            "is_complete": is_complete,
            "missing_slots": missing_slots,
            "can_proceed_disposition": can_proceed,
            "completion_percentage": completion_pct,
            "has_critical_red_flags": has_critical_flags
        }
        
        logger.info(
            f"📊 Validation: complete={is_complete}, "
            f"completion={completion_pct:.1f}%, "
            f"can_proceed={can_proceed}, "
            f"critical_flags={has_critical_flags}, "
            f"missing={len(missing_slots)}"
        )
        
        return result
    
    # ========================================================================
    # 4. CONVERT LEGACY SESSION DATA
    # ========================================================================
    
    def convert_legacy_session_data(self, legacy_data: Dict[str, Any]) -> TriageState:
        """
        Convert legacy flat dict to structured TriageState.
        
        Handles legacy formats where:
        - phase/path are strings instead of Enums
        - Data is flat instead of nested
        - Fields have different names
        
        Args:
            legacy_data: Dict with legacy format
        
        Returns:
            Structured TriageState object
        
        Example:
            >>> legacy = {
            ...     'session_id': '0001_090126',
            ...     'LOCATION': 'Bologna',
            ...     'age': 35,
            ...     'CHIEF_COMPLAINT': 'mal di testa',
            ...     'urgenza': 3
            ... }
            >>> state = bridge.convert_legacy_session_data(legacy)
            >>> state.patient_info.location
            'Bologna'
        """
        logger.info("🔄 Converting legacy session data to TriageState")
        
        # Extract session metadata
        session_id = legacy_data.get("session_id", "UNKNOWN")
        
        # Convert phase (handle string → Enum)
        current_phase = legacy_data.get("current_phase", "INTENT_DETECTION")
        if isinstance(current_phase, str):
            try:
                current_phase = TriagePhase(current_phase)
            except ValueError:
                current_phase = TriagePhase.INTENT_DETECTION
        
        # Convert path (handle string → Enum)
        assigned_path = legacy_data.get("assigned_path")
        if isinstance(assigned_path, str):
            try:
                assigned_path = TriagePath(assigned_path)
            except ValueError:
                assigned_path = None
        
        # Convert branch (handle string → Enum)
        assigned_branch = legacy_data.get("assigned_branch")
        if isinstance(assigned_branch, str):
            try:
                assigned_branch = TriageBranch(assigned_branch)
            except ValueError:
                assigned_branch = None
        
        # Build PatientInfo
        patient_info = PatientInfo(
            age=legacy_data.get("age"),
            sex=legacy_data.get("sex"),
            location=legacy_data.get("LOCATION"),
            pregnant=legacy_data.get("pregnant")
        )
        
        # Build ClinicalData
        red_flags = legacy_data.get("RED_FLAGS", [])
        if isinstance(red_flags, str):
            red_flags = [red_flags] if red_flags.strip() else []
        
        clinical_data = ClinicalData(
            chief_complaint=legacy_data.get("CHIEF_COMPLAINT"),
            pain_scale=legacy_data.get("PAIN_SCALE"),
            duration=legacy_data.get("duration"),
            red_flags=red_flags,
            medications=legacy_data.get("medications"),
            allergies=legacy_data.get("allergies"),
            chronic_conditions=legacy_data.get("chronic_conditions")
        )
        
        # Build TriageMetadata
        metadata = TriageMetadata(
            urgenza=legacy_data.get("urgenza", 3),
            area=legacy_data.get("area", "Generale"),
            confidence=legacy_data.get("confidence", 0.8),
            fallback_used=legacy_data.get("fallback_used", False)
        )
        
        # Build TriageState
        state = TriageState(
            session_id=session_id,
            current_phase=current_phase,
            assigned_path=assigned_path,
            assigned_branch=assigned_branch,
            question_count=legacy_data.get("question_count", 0),
            patient_info=patient_info,
            clinical_data=clinical_data,
            metadata=metadata,
            consent_given=legacy_data.get("consent_given", False)
        )
        
        logger.info(f"✅ Converted legacy data to TriageState (session_id={session_id})")
        
        return state


# ============================================================================
# LEGACY COMPATIBILITY - Keep stream_ai_response
# ============================================================================

import asyncio
from typing import Union, Iterator


def stream_ai_response(
    orchestrator,
    messages,
    path,
    phase,
    collected_data=None,
    is_first_message=False
) -> Iterator[Union[str, Any]]:
    """
    Convert async generator to sync for Streamlit (legacy compatibility).
    
    Wrapper with robust error handling and detailed logging.
    
    Args:
        orchestrator: ModelOrchestrator instance
        messages: List of chat messages
        path: Triage path (A/B/C)
        phase: Current phase (e.g., "ANAMNESIS", "DISPOSITION")
        collected_data: Data already collected during conversation
        is_first_message: True if first contact for intent detection
    
    Yields:
        str: Text token for incremental streaming
        TriageResponse: Final object with metadata
    """
    # Input validation
    if collected_data is None:
        collected_data = {}
    if not isinstance(collected_data, dict):
        logger.error(f"collected_data must be dict, got {type(collected_data)}")
        collected_data = {}
    
    async def _collect():
        items = []
        try:
            logger.info(
                f"Bridge: Starting async collection | "
                f"phase={phase}, path={path}, messages={len(messages)}, "
                f"collected_data_keys={list(collected_data.keys())}, "
                f"is_first={is_first_message}"
            )
            
            # Call orchestrator's streaming method
            async for chunk in orchestrator.call_ai_streaming(
                messages, path, phase, collected_data, is_first_message
            ):
                items.append(chunk)
                
                # Log chunk type
                chunk_type = type(chunk).__name__
                if isinstance(chunk, str):
                    logger.debug(f"Text chunk received: {len(chunk)} chars")
                else:
                    logger.debug(f"Object chunk received: {chunk_type}")
            
            logger.info(f"Bridge: Collection completed | {len(items)} chunks total")
        
        except asyncio.TimeoutError:
            logger.error(f"Bridge: Timeout during generation (phase={phase})")
            items.append("Request took too long. Try a shorter question.")
        
        except Exception as e:
            logger.error(f"Bridge: Error during async collection: {e}", exc_info=True)
            items.append("An error occurred during AI communication. Please try again.")
        
        return items
    
    # Create new event loop for current request
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        results = loop.run_until_complete(_collect())
        logger.info(f"Bridge: Starting to yield {len(results)} items")
        
        for item in results:
            yield item
    
    except Exception as e:
        logger.error(f"Bridge: Critical error in event loop: {e}", exc_info=True)
        yield f"Critical error: {str(e)}"
    
    finally:
        loop.close()
        logger.debug("Bridge: Event loop closed")