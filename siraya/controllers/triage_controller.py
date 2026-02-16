"""
SIRAYA Triage Controller - AI-Driven Orchestrator
V2.0: Single Question Policy + Zero Hardcoded Questions
"""

from enum import Enum
from typing import Dict, Optional, Tuple
import time
import logging
import json

# ✅ Import globale per evitare NameError in contesti Streamlit
from ..core.state_manager import StateKeys

logger = logging.getLogger(__name__)


class TriageBranch(Enum):
    EMERGENCY = "A"          # Red/Orange
    MENTAL_HEALTH = "B"      # Black
    STANDARD = "C"           # Green/Yellow
    INFO = "INFO"            # Richieste informative


class TriagePhase(Enum):
    INTAKE = "intake"
    CHIEF_COMPLAINT = "chief_complaint"  # Raccolta sintomo principale (open text)
    LOCALIZATION = "localization"
    CONSENT = "consent"              # Solo Branch B
    FAST_TRIAGE = "fast_triage"      # Branch A: 3-4 domande emergenza (lowercase per match event store)
    PAIN_SCALE = "pain_scale"
    DEMOGRAPHICS = "demographics"
    CLINICAL_TRIAGE = "clinical_triage"  # Branch C: 5-7 domande (lowercase per match event store)
    RISK_ASSESSMENT = "risk_assessment"  # Branch B: valutazione rischio (lowercase per match event store)
    OUTCOME = "outcome"              # ✅ NEW - Raccomandazione breve + recapiti struttura
    SBAR_GENERATION = "sbar"         # Report completo (background, per download)


class TriageController:
    """Orchestrator che delega all'AI la generazione delle domande."""
    
    def __init__(self):
        from ..core.state_manager import get_state_manager
        from ..services.llm_service import get_llm_service
        from ..services.data_loader import get_data_loader
        from ..services.db_service import get_db_service
        from ..services.rag_service import get_rag_service
        from ..config.settings import EMERGENCY_RULES
        
        self.state_manager = get_state_manager()
        self.llm = get_llm_service()
        self.kb = get_data_loader()
        self.db = get_db_service()
        self.rag = get_rag_service()
        
        # Emergency keywords - Accesso corretto agli attributi di classe
        self.emergency_keywords = (
            EMERGENCY_RULES.CRITICAL_RED_FLAGS +   # Lista keyword emergenze critiche (118 immediato)
            EMERGENCY_RULES.HIGH_RED_FLAGS         # Lista keyword emergenze urgenti (Path A fast-track)
        )
        self.mental_health_keywords = (
            EMERGENCY_RULES.MENTAL_HEALTH_CRISIS +     # Crisi psichiatriche gravi (suicidio, autolesionismo)
            EMERGENCY_RULES.MENTAL_HEALTH_KEYWORDS     # Sintomi salute mentale (ansia, depressione)
        )
        self.info_keywords = EMERGENCY_RULES.INFO_KEYWORDS  # Keywords richieste informative (orari, dove, telefono)
    
    def process_user_input(self, user_input: str) -> dict:
        """
        VERSIONE 3.0: Event-Driven Architecture.
        Ogni azione emette eventi, lo stato è ricostruito dagli eventi.
        
        Returns:
            {
                "assistant_response": str,
                "question_type": "multiple_choice" | "open_text" | "outcome" | "sbar",
                "options": List[str] | None,
                "metadata": Dict,
                "processing_time_ms": int
            }
        """
        from ..core.event_store import get_event_store, EventType
        
        event_store = get_event_store()
        start_time = time.time()
        session_id = self.state_manager.get(StateKeys.SESSION_ID, "unknown")
        
        # ✅ STEP 1: Recupera stato da eventi (non da session state)
        collected_data = event_store.get_collected_data_from_events()
        current_phase = event_store.get_current_phase_from_events()
        current_branch = self.state_manager.get(StateKeys.TRIAGE_BRANCH)
        
        # Fallback a session state se eventi non disponibili (backward compatibility)
        if not collected_data:
            collected_data = self.state_manager.get(StateKeys.COLLECTED_DATA, {})
        if current_phase == "intake" and not event_store.get_events():
            current_phase = self.state_manager.get(StateKeys.CURRENT_PHASE, TriagePhase.INTAKE.value)
        
        logger.info(f"📍 Stato da eventi: branch={current_branch}, phase={current_phase}")
        
        # ✅ STEP 2: Classifica branch (solo prima volta)
        if not current_branch:
            current_branch = self._classify_branch(user_input)
            self.state_manager.set(StateKeys.TRIAGE_PATH, current_branch.value)
            self.state_manager.set(StateKeys.TRIAGE_BRANCH, current_branch.value)
            
            # Emetti evento BRANCH_CLASSIFIED
            event_store.emit(
                EventType.BRANCH_CLASSIFIED,
                phase="intake",
                data={"branch": current_branch.value, "user_input": user_input}
            )
            logger.info(f"✅ Branch classificato: {current_branch.value}")
        else:
            current_branch = TriageBranch(current_branch)
        
        # ✅ STEP 3: Estrai dati (slot filling unificato con dual keys)
        extracted = self._extract_data_unified(user_input, collected_data)
        if extracted:
            collected_data.update(extracted)
            
            # Emetti evento DATA_EXTRACTED
            event_store.emit(
                EventType.DATA_EXTRACTED,
                phase=current_phase,
                data={"extracted": extracted, "user_input": user_input}
            )
            logger.info(f"✅ Dati estratti: {list(extracted.keys())}")
        
        # Salva in session state per backward compatibility UI
        self.state_manager.set(StateKeys.COLLECTED_DATA, collected_data)
        
        # ✅ STEP 4: Verifica memoria Supabase per dati persistenti
        known_data = self._fetch_known_data_from_history()
        if known_data:
            collected_data.update(known_data)
            # Emetti anche dati persistenti come evento
            event_store.emit(
                EventType.DATA_EXTRACTED,
                phase=current_phase,
                data={"extracted": known_data, "source": "persistent_history"}
            )
        
        # ✅ STEP 5: Determina fase successiva (FSM event-driven)
        next_phase = self._determine_next_phase_event_driven(
            branch=current_branch,
            current_phase=TriagePhase(current_phase) if current_phase else TriagePhase.INTAKE,
            collected_data=collected_data,
            event_store=event_store
        )
        
        # Se cambio fase, emetti evento PHASE_ENTERED
        if next_phase.value != current_phase:
            event_store.emit(
                EventType.PHASE_ENTERED,
                phase=next_phase.value,
                data={"from_phase": current_phase, "to_phase": next_phase.value}
            )
            logger.info(f"🔄 Transizione fase: {current_phase} → {next_phase.value}")
        
        self.state_manager.set(StateKeys.CURRENT_PHASE, next_phase.value)
        
        # ✅ STEP 6: Genera prossima domanda
        next_question = self._generate_question_ai(
            branch=current_branch,
            phase=next_phase,
            collected_data=collected_data,
            question_count=0,  # Deprecato, calcolato da eventi
            user_input=user_input
        )
        
        # Emetti evento QUESTION_ASKED (solo se non è outcome/sbar)
        if next_phase not in [TriagePhase.SBAR_GENERATION, TriagePhase.OUTCOME]:
            event_store.emit(
                EventType.QUESTION_ASKED,
                phase=next_phase.value,
                data={
                    "question": next_question["text"],
                    "type": next_question["type"],
                    "options": next_question.get("options"),
                    "user_input": user_input
                }
            )
        
        # ✅ STEP 7: Prepara risposta
        processing_time = int((time.time() - start_time) * 1000)
        
        result = {
            "assistant_response": next_question["text"],
            "question_type": next_question["type"],
            "options": next_question.get("options"),
            "metadata": next_question.get("metadata", {}),
            "processing_time_ms": processing_time
        }
        
        # Salva risposta nello state per chat_view.py
        self.state_manager.set(StateKeys.LAST_BOT_RESPONSE, result)
        
        # ✅ STEP 8: Salva su Supabase (legacy, per compatibilità)
        self.db.save_interaction(
            session_id=session_id,
            user_input=user_input,
            assistant_response=next_question["text"],
            processing_time_ms=processing_time,
            session_state={
                "triage_path": current_branch.value,
                "current_phase": next_phase.value,
                "collected_data": collected_data,
                "urgency_level": self._get_urgency_level(current_branch)
            },
            metadata=next_question.get("metadata", {})
        )
        
        return result
    
    def _classify_branch(self, user_input: str) -> TriageBranch:
        """
        Classifica intent in Branch A/B/C/INFO secondo diagramma di flusso V3.
        
        Priorità:
        1. Keyword matching (veloce e preciso)
        2. AI classification (per casi ambigui)
        3. Default STANDARD (per saluti generici)
        """
        user_lower = user_input.lower().strip()
        
        # ✅ STEP 1: Keyword matching per emergenze (Branch A)
        # Secondo diagramma: dolore toracico, emorragia, trauma, svenimento, difficoltà respiratorie
        if any(kw in user_lower for kw in self.emergency_keywords):
            logger.info(f"✅ Branch A (EMERGENCY) rilevato via keyword: {user_input[:50]}")
            return TriageBranch.EMERGENCY
        
        # ✅ STEP 2: Keyword matching per salute mentale (Branch B)
        # Secondo diagramma: depressione, suicidio, ansia grave, autolesionismo
        if any(kw in user_lower for kw in self.mental_health_keywords):
            logger.info(f"✅ Branch B (MENTAL_HEALTH) rilevato via keyword: {user_input[:50]}")
            return TriageBranch.MENTAL_HEALTH
        
        # ✅ STEP 3: Keyword matching per richieste informative (Branch INFO)
        # Secondo diagramma: orari, dove, telefono, come funziona, prenotare
        if any(kw in user_lower for kw in self.info_keywords):
            logger.info(f"✅ Branch INFO rilevato via keyword: {user_input[:50]}")
            return TriageBranch.INFO
        
        # ✅ STEP 4: Saluti generici → STANDARD (default sicuro)
        generic_greetings = ["ciao", "buongiorno", "buonasera", "salve", "hey", "hello", "buondì"]
        if any(greet in user_lower for greet in generic_greetings) or len(user_input.strip()) < 10:
            logger.info(f"✅ Saluto generico o messaggio breve, classifico come STANDARD (Branch C)")
            return TriageBranch.STANDARD
        
        # ✅ STEP 5: AI classification per messaggi specifici ma ambigui
        try:
            prompt = f"""Sei un assistente medico esperto in triage telefonico. Classifica questo messaggio in UNA delle 4 categorie seguenti.

**Input utente:** "{user_input}"

**Categorie disponibili:**

1. **EMERGENCY** (Branch A - Codice Rosso/Arancione):
   - Sintomi gravi che richiedono Pronto Soccorso immediato
   - Esempi: dolore toracico, emorragia, trauma cranico, svenimento, difficoltà respiratorie gravi, paralisi
   - Se il paziente descrive sintomi che suggeriscono emergenza medica → EMERGENCY

2. **MENTAL_HEALTH** (Branch B - Salute Mentale):
   - Crisi psichiatrica, ideazione suicidaria, autolesionismo
   - Depressione grave, ansia paralizzante, attacchi di panico
   - Se il paziente menziona pensieri di autolesionismo/suicidio → MENTAL_HEALTH

3. **INFO** (Richieste Informative):
   - Domande su orari, localizzazione servizi, telefoni, come funziona un servizio
   - Richieste di prenotazione, informazioni su strutture
   - Se il paziente chiede informazioni (non descrive sintomi) → INFO

4. **STANDARD** (Branch C - Triage Standard):
   - Sintomi non urgenti: mal di testa, dolori addominali, febbre lieve, mal di gola
   - Disturbi comuni che richiedono valutazione ma non emergenza
   - Default per sintomi generici → STANDARD

**Regola importante:** Se il messaggio contiene sia sintomi che richieste informative, classifica in base al CONTENUTO PRINCIPALE (sintomi > info).

Rispondi SOLO in JSON (nessun altro testo):
{{
    "classification": "EMERGENCY" | "MENTAL_HEALTH" | "STANDARD" | "INFO",
    "reasoning": "Breve spiegazione (max 20 parole)"
}}
"""
            
            response = self.llm.generate_with_json_parse(prompt, temperature=0.0, max_tokens=50)
            
            if isinstance(response, dict) and "classification" in response:
                classification = response["classification"].strip().upper()
                reasoning = response.get("reasoning", "")
                
                if classification in ["EMERGENCY", "MENTAL_HEALTH", "STANDARD", "INFO"]:
                    logger.info(f"✅ AI classification: {classification} (reasoning: {reasoning})")
                    return TriageBranch[classification]
            
            logger.warning(f"⚠️ Classificazione AI non valida: {response}, uso STANDARD")
            
        except Exception as e:
            logger.error(f"❌ Errore classify_branch AI: {e}")
        
        # Default sicuro: STANDARD (Branch C)
        logger.info(f"✅ Default: classifico come STANDARD (Branch C)")
        return TriageBranch.STANDARD
    
    def _extract_data_unified(self, user_input: str, current_data: Dict) -> Dict:
        """
        Slot filling UNIFICATO con normalizzazione chiavi.
        Tutte le chiavi hanno alias per evitare mismatch.
        
        Dual keys:
        - main_symptom = chief_complaint (alias)
        - location = current_location (alias)
        """
        import re
        extracted = {}
        user_lower = user_input.lower()
        
        # ✅ SINTOMO PRINCIPALE (chiave unificata)
        # Salva con ENTRAMBE le chiavi: main_symptom E chief_complaint
        symptom_keywords = ["dolore", "mal di", "sintomo", "problema", "fastidio", "ho", "mi fa"]
        if any(kw in user_lower for kw in symptom_keywords) and len(user_input.strip()) > 5:
            # Estrai sintomo (primi 100 caratteri)
            symptom_raw = user_input.strip()[:100]
            extracted["main_symptom"] = symptom_raw  # Chiave primaria
            extracted["chief_complaint"] = symptom_raw  # Alias per UI
            logger.info(f"✅ Sintomo estratto (dual-key): {symptom_raw[:30]}")
        
        # ✅ SCALA DOLORE
        pain_patterns = [
            r'(?:scala|dolore|intensità)[:\s]*(\d{1,2})',
            r'(\d{1,2})\s*su\s*10',
            r'(\d{1,2})/10',
            r'(\d{1,2})\s*-\s*(\d{1,2}):\s*dolore',  # Match "9-10: Dolore estremo"
            r'^(\d{1,2})$',  # Risposta secca "6"
        ]
        
        for pattern in pain_patterns:
            match = re.search(pattern, user_lower)
            if match:
                try:
                    scale = int(match.group(1))
                    if 1 <= scale <= 10:
                        extracted['pain_scale'] = scale
                        logger.info(f"✅ Dolore estratto: {scale}/10")
                        break
                except:
                    pass
        
        # ✅ ETÀ
        age_patterns = [
            r'(\d{1,3})\s*ann[io]',
            r'ho\s*(\d{1,3})',
            r'^(\d{1,3})$',  # Risposta secca "54"
        ]
        
        for pattern in age_patterns:
            match = re.search(pattern, user_lower)
            if match:
                age = int(match.group(1))
                if 0 < age < 120:
                    extracted['age'] = age
                    logger.info(f"✅ Età estratta: {age}")
                    break
        
        # ✅ LOCALITÀ
        comuni_er = [
            "bologna", "modena", "parma", "reggio emilia", "piacenza",
            "ferrara", "ravenna", "forlì", "cesena", "rimini",
            "imola", "faenza", "lugo", "cervia", "riccione", "cattolica"
        ]
        
        for comune in comuni_er:
            if comune in user_lower:
                extracted['location'] = comune.title()
                extracted['current_location'] = comune.title()  # Alias
                logger.info(f"✅ Località estratta: {comune}")
                break
        
        return extracted
    
    def _extract_data_ai(self, user_input: str, current_data: Dict) -> Dict:
        """
        DEPRECATED: Usa _extract_data_unified() invece.
        Mantenuto per backward compatibility.
        """
        return self._extract_data_unified(user_input, current_data)
        """
        Slot filling ROBUSTO con pattern matching + AI fallback.
        
        Estrae: location, age, gender, pain_scale, main_symptom, onset, medications
        """
        import re
        extracted = {}
        user_lower = user_input.lower()
        
        # PATTERN 1: Scala dolore (keyword + numeri)
        pain_patterns = [
            r'(?:scala|dolore|intensità|livello)[:\s]*(\d{1,2})',
            r'(\d{1,2})\s*su\s*10',
            r'(\d{1,2})/10',
            r'sempre\s*(\d{1,2})',  # ← FIX per "sempre 6"
            r'^(\d{1,2})$'  # ← FIX per risposta numerica secca "6"
        ]
        
        for pattern in pain_patterns:
            match = re.search(pattern, user_lower)
            if match:
                scale = int(match.group(1))
                if 1 <= scale <= 10:
                    extracted['pain_scale'] = scale
                    logger.info(f"✅ Estratto pain_scale via regex: {scale}")
                    break
        
        # PATTERN 2: Età
        age_patterns = [
            r'(\d{1,3})\s*ann[io]',
            r'ho\s*(\d{1,3})\s*ann',
            r'età[:\s]*(\d{1,3})'
        ]
        
        for pattern in age_patterns:
            match = re.search(pattern, user_lower)
            if match:
                age = int(match.group(1))
                if 0 < age < 120:
                    extracted['age'] = age
                    logger.info(f"✅ Estratto age via regex: {age}")
                    break
        
        # PATTERN 3: Località (comuni ER)
        comuni_er = [
            "bologna", "modena", "parma", "reggio emilia", "piacenza",
            "ferrara", "ravenna", "forlì", "cesena", "rimini",
            "imola", "faenza", "lugo", "cervia", "riccione"
        ]
        
        for comune in comuni_er:
            if comune in user_lower:
                extracted['location'] = comune.title()
                logger.info(f"✅ Estratto location via keyword: {comune}")
                break
        
        # PATTERN 4: Onset temporale (ieri, oggi, stamattina)
        onset_patterns = {
            r'ieri': 'ieri',
            r'stamattina|questa mattina': 'stamattina',
            r'stasera|questa sera': 'stasera',
            r'oggi': 'oggi',
            r'(\d+)\s*(?:ore|ora)': lambda m: f"{m.group(1)} ore fa",
            r'(\d+)\s*giorn[io]': lambda m: f"{m.group(1)} giorni fa"
        }
        
        for pattern, value in onset_patterns.items():
            match = re.search(pattern, user_lower)
            if match:
                if callable(value):
                    extracted['onset'] = value(match)
                else:
                    extracted['onset'] = value
                logger.info(f"✅ Estratto onset: {extracted['onset']}")
                break
        
        # FALLBACK AI: Se regex non ha trovato nulla, chiedi all'AI
        if not extracted:
            prompt = f"""
            Estrai dati strutturati da: "{user_input}"
            
            Dati da cercare:
            - pain_scale: Numero 1-10 (se menzionato)
            - age: Età in anni
            - location: Comune Emilia-Romagna
            - main_symptom: Sintomo principale
            - onset: Quando è iniziato (es: "ieri", "stamattina")
            
            JSON output (usa null se assente):
            {{
                "pain_scale": null,
                "age": null,
                "location": null,
                "main_symptom": null,
                "onset": null
            }}
            """
            
            try:
                response = self.llm.generate_with_json_parse(prompt, temperature=0.0)
                if isinstance(response, dict):
                    for key, value in response.items():
                        if value and value != "null":
                            extracted[key] = value
                    logger.info(f"✅ Estratto via AI fallback: {list(extracted.keys())}")
            except Exception as e:
                logger.warning(f"⚠️ AI fallback extraction failed: {e}")
        
        return extracted
    
    def _fetch_known_data_from_history(self) -> Dict:
        """
        Recupera dati già noti da Supabase per evitare domande duplicate.
        
        Dati persistenti tra sessioni:
        - age, location, chronic_conditions, allergies, medications
        """
        user_id = self.state_manager.get(StateKeys.USER_ID, "anonymous")
        session_id = self.state_manager.get(StateKeys.SESSION_ID, "unknown")
        
        # Se utente anonimo, prova con session_id
        if user_id == "anonymous":
            user_id = session_id
        
        try:
            history = self.db.fetch_user_history(user_id, limit=30)
            
            known = {}
            for entry in history:
                # metadata è già un dict (non JSON string)
                old_metadata = entry.get("metadata", {})
                
                # Se metadata è stringa JSON, parsala
                if isinstance(old_metadata, str):
                    try:
                        old_metadata = json.loads(old_metadata)
                    except:
                        old_metadata = {}
                
                # Cerca in collected_data storico (se presente nel metadata)
                old_collected = old_metadata.get("collected_data", {})
                
                # Merge dati persistenti (NON sintomi attuali)
                persistent_keys = ["age", "location", "current_location", "chronic_conditions", "allergies", "medications"]
                for key in persistent_keys:
                    if key in old_collected and key not in known:
                        known[key] = old_collected[key]
            
            if known:
                logger.info(f"✅ Dati recuperati da storia: {list(known.keys())}")
            
            return known
        
        except Exception as e:
            logger.warning(f"⚠️ Impossibile recuperare storia: {e}")
            return {}
    
    def _determine_next_phase(
        self, 
        branch: TriageBranch, 
        current_phase: TriagePhase,
        collected_data: Dict,
        question_count: int  # Legacy parameter - mantenuto per compatibilità
    ) -> TriagePhase:
        """
        FSM con FORCE ADVANCE: Se dato già presente, salta fase automaticamente.
        """
        
        # Branch C: Triage Standard
        # Sequenza corretta: INTAKE → CHIEF_COMPLAINT → LOCALIZATION → PAIN_SCALE → DEMOGRAPHICS → CLINICAL_TRIAGE → SBAR
        if branch == TriageBranch.STANDARD:
            
            # FASE 0: INTAKE (punto di partenza)
            if current_phase == TriagePhase.INTAKE:
                # Se sintomo già presente, salta a localizzazione
                if "main_symptom" in collected_data:
                    logger.info("✅ Sintomo già raccolto, salto a LOCALIZATION")
                    # Se anche località presente, vai a pain_scale
                    if "location" in collected_data or "current_location" in collected_data:
                        logger.info("✅ Località già nota, salto a PAIN_SCALE")
                        return TriagePhase.PAIN_SCALE
                    return TriagePhase.LOCALIZATION
                # Altrimenti vai a raccolta sintomo
                return TriagePhase.CHIEF_COMPLAINT
            
            # FASE 1: Raccolta Sintomo Principale (CHIEF_COMPLAINT)
            if current_phase == TriagePhase.CHIEF_COMPLAINT:
                # FORCE ADVANCE: Se sintomo raccolto, vai a località
                if "main_symptom" in collected_data:
                    # Se anche località già nota, salta direttamente a pain_scale
                    if "location" in collected_data or "current_location" in collected_data:
                        logger.info("✅ Sintomo + Località già noti, salto a PAIN_SCALE")
                        return TriagePhase.PAIN_SCALE
                    logger.info("✅ Sintomo raccolto, avanzo a LOCALIZATION")
                    return TriagePhase.LOCALIZATION
                # Se sintomo manca, rimani qui
                return TriagePhase.CHIEF_COMPLAINT
            
            # FASE 2: Localizzazione
            if current_phase == TriagePhase.LOCALIZATION:
                # FORCE ADVANCE: Se location presente, vai a pain_scale
                if "location" in collected_data or "current_location" in collected_data:
                    logger.info("✅ Località raccolta, avanzo a PAIN_SCALE")
                    return TriagePhase.PAIN_SCALE
                return TriagePhase.LOCALIZATION  # Rimani solo se manca
            
            # FASE 3: Pain Scale
            if current_phase == TriagePhase.PAIN_SCALE:
                # FORCE ADVANCE: Se pain_scale presente, vai a demographics
                if "pain_scale" in collected_data:
                    logger.info(f"✅ Scala dolore raccolta: {collected_data['pain_scale']}, avanzo a DEMOGRAPHICS")
                    return TriagePhase.DEMOGRAPHICS
                return TriagePhase.PAIN_SCALE  # Rimani solo se manca
            
            # FASE 4: Demographics (età)
            if current_phase == TriagePhase.DEMOGRAPHICS:
                # FORCE ADVANCE: Se età presente, vai a clinical triage
                if "age" in collected_data:
                    logger.info(f"✅ Età raccolta: {collected_data['age']}, avanzo a CLINICAL_TRIAGE")
                    # ✅ RESET COUNTER CLINICO: Quando entriamo in CLINICAL_TRIAGE, resettiamo SOLO il counter clinico
                    # Mantieni storico intake (non resettare)
                    self.state_manager.set(StateKeys.QUESTION_COUNT_CLINICAL, 0)
                    logger.info("✅ Entrando in CLINICAL_TRIAGE - Counter clinico resettato")
                    return TriagePhase.CLINICAL_TRIAGE
                return TriagePhase.DEMOGRAPHICS
            
            # FASE 5: Clinical Triage (5-7 domande basate su protocolli)
            if current_phase == TriagePhase.CLINICAL_TRIAGE:
                # ✅ Usa counter clinico specifico (non question_count globale)
                clinical_count = self.state_manager.get(StateKeys.QUESTION_COUNT_CLINICAL, 0)
                
                # Verifica dati minimi per OUTCOME
                required_data = ["main_symptom", "pain_scale", "age"]
                has_location = "location" in collected_data or "current_location" in collected_data
                has_required = all(key in collected_data for key in required_data)
                
                # Avanza a OUTCOME se:
                # - Almeno 5 domande clinical E dati completi
                # - OPPURE max 7 domande clinical raggiunte
                if clinical_count >= 5 and has_location and has_required:
                    logger.info(f"✅ {clinical_count} domande clinical completate → OUTCOME")
                    return TriagePhase.OUTCOME
                
                if clinical_count >= 7:
                    logger.warning(f"⚠️ Max 7 domande clinical → forzo OUTCOME")
                    return TriagePhase.OUTCOME
                
                # Continua clinical triage
                logger.info(f"⏸️ Clinical triage continua (domanda {clinical_count + 1}/5-7)")
                return TriagePhase.CLINICAL_TRIAGE
            
            # FASE 6: SBAR (report finale)
            if current_phase == TriagePhase.SBAR_GENERATION:
                return TriagePhase.SBAR_GENERATION
        
        # Branch A: Emergency
        if branch == TriageBranch.EMERGENCY:
            if current_phase == TriagePhase.INTAKE:
                if "location" in collected_data or "current_location" in collected_data:
                    # ✅ RESET COUNTER CLINICO: Quando entriamo in FAST_TRIAGE, resettiamo
                    self.state_manager.set(StateKeys.QUESTION_COUNT_CLINICAL, 0)
                    logger.info("✅ Entrando in FAST_TRIAGE - Counter clinico resettato")
                    return TriagePhase.FAST_TRIAGE
                return TriagePhase.LOCALIZATION
            
            if current_phase == TriagePhase.LOCALIZATION:
                if "location" in collected_data or "current_location" in collected_data:
                    # ✅ RESET COUNTER CLINICO
                    self.state_manager.set(StateKeys.QUESTION_COUNT_CLINICAL, 0)
                    logger.info("✅ Entrando in FAST_TRIAGE - Counter clinico resettato")
                    return TriagePhase.FAST_TRIAGE
                return TriagePhase.LOCALIZATION
            
            if current_phase == TriagePhase.FAST_TRIAGE:
                # ✅ Usa counter clinico specifico
                clinical_count = self.state_manager.get(StateKeys.QUESTION_COUNT_CLINICAL, 0)
                if clinical_count >= 3:  # Min 3 domande per emergenza
                    logger.info(f"✅ {clinical_count} domande fast-triage completate → OUTCOME")
                    return TriagePhase.OUTCOME
                logger.info(f"⏸️ Fast triage continua (domanda {clinical_count + 1}/3-4)")
                return TriagePhase.FAST_TRIAGE
            
            if current_phase == TriagePhase.OUTCOME:
                return TriagePhase.OUTCOME
            if current_phase == TriagePhase.SBAR_GENERATION:
                return TriagePhase.SBAR_GENERATION
        
        # Branch B: Mental Health
        if branch == TriageBranch.MENTAL_HEALTH:
            if current_phase == TriagePhase.INTAKE:
                return TriagePhase.CONSENT
            
            if current_phase == TriagePhase.CONSENT:
                if collected_data.get("consent") == "yes":
                    return TriagePhase.DEMOGRAPHICS
                return TriagePhase.OUTCOME  # Se rifiuta consenso, vai a OUTCOME (con hotline)
            
            if current_phase == TriagePhase.DEMOGRAPHICS:
                if "age" in collected_data:
                    # ✅ RESET COUNTER CLINICO: Quando entriamo in RISK_ASSESSMENT, resettiamo
                    self.state_manager.set(StateKeys.QUESTION_COUNT_CLINICAL, 0)
                    logger.info("✅ Entrando in RISK_ASSESSMENT - Counter clinico resettato")
                    return TriagePhase.RISK_ASSESSMENT
                return TriagePhase.DEMOGRAPHICS
            
            if current_phase == TriagePhase.RISK_ASSESSMENT:
                # ✅ Usa counter clinico specifico
                clinical_count = self.state_manager.get(StateKeys.QUESTION_COUNT_CLINICAL, 0)
                if clinical_count >= 4:
                    logger.info(f"✅ {clinical_count} domande risk assessment completate → OUTCOME")
                    return TriagePhase.OUTCOME
                logger.info(f"⏸️ Risk assessment continua (domanda {clinical_count + 1}/4-5)")
                return TriagePhase.RISK_ASSESSMENT
            
            if current_phase == TriagePhase.OUTCOME:
                return TriagePhase.OUTCOME
            if current_phase == TriagePhase.SBAR_GENERATION:
                return TriagePhase.SBAR_GENERATION
        
        # Fallback sicuro
        logger.warning(f"⚠️ FSM fallback: branch={branch}, phase={current_phase}")
        return current_phase
    
    def _determine_next_phase_event_driven(
        self,
        branch: TriageBranch,
        current_phase: TriagePhase,
        collected_data: Dict,
        event_store
    ) -> TriagePhase:
        """
        FSM con logica event-driven: conta domande dalla event store.
        Più affidabile del counter globale.
        """
        from ..core.event_store import EventType
        
        # Branch C: STANDARD
        if branch == TriageBranch.STANDARD:
            
            if current_phase == TriagePhase.INTAKE:
                has_symptom = any(k in collected_data for k in ["main_symptom", "chief_complaint"])
                if has_symptom:
                    if any(k in collected_data for k in ["location", "current_location"]):
                        return TriagePhase.PAIN_SCALE
                    return TriagePhase.LOCALIZATION
                return TriagePhase.CHIEF_COMPLAINT
            
            if current_phase == TriagePhase.CHIEF_COMPLAINT:
                has_symptom = any(k in collected_data for k in ["main_symptom", "chief_complaint"])
                if has_symptom:
                    if any(k in collected_data for k in ["location", "current_location"]):
                        return TriagePhase.PAIN_SCALE
                    return TriagePhase.LOCALIZATION
                return TriagePhase.CHIEF_COMPLAINT
            
            if current_phase == TriagePhase.LOCALIZATION:
                if any(k in collected_data for k in ["location", "current_location"]):
                    return TriagePhase.PAIN_SCALE
                return TriagePhase.LOCALIZATION
            
            if current_phase == TriagePhase.PAIN_SCALE:
                if "pain_scale" in collected_data:
                    return TriagePhase.DEMOGRAPHICS
                return TriagePhase.PAIN_SCALE
            
            if current_phase == TriagePhase.DEMOGRAPHICS:
                if "age" in collected_data:
                    return TriagePhase.CLINICAL_TRIAGE
                return TriagePhase.DEMOGRAPHICS
            
            if current_phase == TriagePhase.CLINICAL_TRIAGE:
                # ✅ Conta domande dalla event store
                clinical_questions = event_store.count_questions_in_phase("clinical_triage")
                
                has_symptom = any(k in collected_data for k in ["main_symptom", "chief_complaint"])
                has_location = any(k in collected_data for k in ["location", "current_location"])
                has_pain = "pain_scale" in collected_data
                has_age = "age" in collected_data
                has_required = has_symptom and has_pain and has_age
                
                # Vai a OUTCOME se:
                # - Almeno 5 domande clinical E dati completi
                # - OPPURE 7 domande (max assoluto)
                if clinical_questions >= 5 and has_location and has_required:
                    logger.info(f"✅ {clinical_questions} domande clinical + dati OK → OUTCOME")
                    return TriagePhase.OUTCOME
                
                if clinical_questions >= 7:
                    logger.warning(f"⚠️ Max 7 domande clinical → forzo OUTCOME")
                    return TriagePhase.OUTCOME
                
                logger.info(f"⏸️ Clinical triage continua ({clinical_questions + 1}/5-7)")
                return TriagePhase.CLINICAL_TRIAGE
            
            if current_phase == TriagePhase.OUTCOME:
                return TriagePhase.OUTCOME
            
            if current_phase == TriagePhase.SBAR_GENERATION:
                return TriagePhase.SBAR_GENERATION
        
        # Branch A: EMERGENCY
        if branch == TriageBranch.EMERGENCY:
            if current_phase == TriagePhase.INTAKE:
                if any(k in collected_data for k in ["location", "current_location"]):
                    return TriagePhase.FAST_TRIAGE
                return TriagePhase.LOCALIZATION
            
            if current_phase == TriagePhase.LOCALIZATION:
                if any(k in collected_data for k in ["location", "current_location"]):
                    return TriagePhase.FAST_TRIAGE
                return TriagePhase.LOCALIZATION
            
            if current_phase == TriagePhase.FAST_TRIAGE:
                fast_questions = event_store.count_questions_in_phase("fast_triage")
                
                if fast_questions >= 3:
                    logger.info(f"✅ {fast_questions} domande fast-triage → OUTCOME")
                    return TriagePhase.OUTCOME
                
                logger.info(f"⏸️ Fast triage continua ({fast_questions + 1}/3-4)")
                return TriagePhase.FAST_TRIAGE
            
            if current_phase in [TriagePhase.OUTCOME, TriagePhase.SBAR_GENERATION]:
                return current_phase
        
        # Branch B: MENTAL_HEALTH
        if branch == TriageBranch.MENTAL_HEALTH:
            if current_phase == TriagePhase.INTAKE:
                return TriagePhase.CONSENT
            
            if current_phase == TriagePhase.CONSENT:
                if collected_data.get("consent") == "yes":
                    return TriagePhase.DEMOGRAPHICS
                return TriagePhase.OUTCOME  # Rifiuto consenso → outcome con hotline
            
            if current_phase == TriagePhase.DEMOGRAPHICS:
                if "age" in collected_data:
                    return TriagePhase.RISK_ASSESSMENT
                return TriagePhase.DEMOGRAPHICS
            
            if current_phase == TriagePhase.RISK_ASSESSMENT:
                risk_questions = event_store.count_questions_in_phase("risk_assessment")
                
                if risk_questions >= 4:
                    logger.info(f"✅ {risk_questions} domande risk → OUTCOME")
                    return TriagePhase.OUTCOME
                
                logger.info(f"⏸️ Risk assessment continua ({risk_questions + 1}/4-5)")
                return TriagePhase.RISK_ASSESSMENT
            
            if current_phase in [TriagePhase.OUTCOME, TriagePhase.SBAR_GENERATION]:
                return current_phase
        
        # Fallback
        logger.warning(f"⚠️ No transition for {branch}/{current_phase}")
        return current_phase
    
    def _generate_question_ai(
        self,
        branch: TriageBranch,
        phase: TriagePhase,
        collected_data: Dict,
        question_count: int,
        user_input: str
    ) -> Dict:
        """
        CUORE DEL SISTEMA: Genera prossima domanda tramite AI.
        
        L'AI decide:
        - Testo domanda
        - Tipo (open_text / multiple_choice / sbar)
        - Opzioni A/B/C (se multiple choice)
        
        NO hardcoded questions!
        """
        
        # ✅ NUOVO: Gestione fase OUTCOME (raccomandazione breve)
        if phase == TriagePhase.OUTCOME:
            return self._generate_outcome_ai(branch, collected_data)
        
        # Se fase SBAR → genera report completo (legacy/fallback)
        if phase == TriagePhase.SBAR_GENERATION:
            return self._generate_sbar_with_logs(branch, collected_data)
        
        # Recupera contesto RAG se fase clinica (RAG temporaneamente disabilitato)
        rag_context = ""
        if phase in [TriagePhase.FAST_TRIAGE, TriagePhase.CLINICAL_TRIAGE, TriagePhase.RISK_ASSESSMENT]:
            try:
                # ✅ RAG è disabilitato (ritorna lista vuota), ma chiamiamo comunque per logging
                rag_docs = self.rag.retrieve_context(
                    query=collected_data.get("main_symptom", user_input),
                    k=3
                )
                # rag_docs sarà sempre [] (RAG disabilitato), quindi rag_context rimane ""
                if rag_docs:
                    rag_context = "\n".join([doc.get("content", "") for doc in rag_docs])
                    logger.info(f"✅ RAG context recuperato: {len(rag_docs)} chunks")
                else:
                    logger.debug(f"ℹ️ RAG disabilitato, AI userà conoscenza generale per: {collected_data.get('main_symptom', user_input[:50])}")
            except Exception as e:
                # ✅ RAG disabilitato, questo errore non dovrebbe mai verificarsi, ma gestiamolo comunque
                logger.debug(f"ℹ️ RAG non disponibile (normale se disabilitato): {type(e).__name__}")
                rag_context = ""
        
        # Prompt AI per generazione domanda
        prompt = self._build_question_generation_prompt(
            branch=branch,
            phase=phase,
            collected_data=collected_data,
            question_count=question_count,
            rag_context=rag_context
        )
        
        try:
            response = self.llm.generate_with_json_parse(prompt, temperature=0.2, max_tokens=300)
            
            # ✅ VALIDAZIONE TIPO DOMANDA
            # Recupera tipo atteso dalla configurazione fase
            phase_config = {
                TriagePhase.CHIEF_COMPLAINT: {"type": "open_text"},
                TriagePhase.LOCALIZATION: {"type": "open_text"},
                TriagePhase.CONSENT: {"type": "multiple_choice"},
                TriagePhase.FAST_TRIAGE: {"type": "multiple_choice"},
                TriagePhase.PAIN_SCALE: {"type": "multiple_choice"},
                TriagePhase.DEMOGRAPHICS: {"type": "open_text"},
                TriagePhase.CLINICAL_TRIAGE: {"type": "multiple_choice"},
                TriagePhase.RISK_ASSESSMENT: {"type": "multiple_choice"}
            }
            
            expected_type = phase_config.get(phase, {}).get("type", "open_text")
            actual_type = response.get("type", "open_text")
            
            # Se AI ha restituito tipo sbagliato, CORREGGI
            if actual_type != expected_type:
                logger.warning(f"⚠️ AI ha restituito type='{actual_type}' ma ci aspettavamo '{expected_type}', correggo")
                response["type"] = expected_type
                
                # Se doveva essere open_text ma ha generato options, rimuovile
                if expected_type == "open_text":
                    response["options"] = None
                    logger.info("✅ Rimosso options per open_text")
                
                # Se doveva essere multiple_choice ma mancano options, genera fallback
                if expected_type == "multiple_choice" and not response.get("options"):
                    logger.error(f"❌ AI non ha generato options per multiple_choice, uso fallback")
                    response["options"] = ["Sì", "No", "Non so"]
            
            # Validazione aggiuntiva: se multiple_choice, assicurati che options sia lista
            if response.get("type") == "multiple_choice":
                if not isinstance(response.get("options"), list) or len(response.get("options", [])) == 0:
                    logger.error(f"❌ Options non valide per multiple_choice: {response.get('options')}")
                    response["options"] = ["Sì", "No", "Non so"]
            
            return {
                "text": response.get("question", "Puoi dirmi di più sui tuoi sintomi?"),
                "type": response.get("type", expected_type),
                "options": response.get("options"),
                "metadata": {"ai_generated": True, "phase": phase.value, "type_corrected": actual_type != expected_type}
            }
        except Exception as e:
            logger.error(f"❌ Errore generate_question_ai: {e}")
            # Fallback sicuro: usa tipo corretto per la fase
            fallback_type = phase_config.get(phase, {}).get("type", "open_text")
            return {
                "text": "Puoi dirmi di più sui tuoi sintomi?",
                "type": fallback_type,
                "options": None if fallback_type == "open_text" else ["Sì", "No"],
                "metadata": {"ai_generated": False, "fallback": True}
            }
    
    def _build_question_generation_prompt(
        self,
        branch: TriageBranch,
        phase: TriagePhase,
        collected_data: Dict,
        question_count: int,
        rag_context: str
    ) -> str:
        """Costruisce prompt per AI che genera la domanda CON TIPO VINCOLATO."""
        
        # ✅ Definisci obiettivo fase + TIPO OBBLIGATORIO per ogni fase
        phase_config = {
            TriagePhase.CHIEF_COMPLAINT: {
                "objective": "Raccogliere sintomo principale o motivo del contatto. Domanda aperta ed empatica.",
                "type": "open_text",  # ← TIPO FORZATO
                "example": "Qual è il motivo del tuo contatto oggi? Posso aiutarti con un sintomo o hai bisogno di informazioni?"
            },
            TriagePhase.LOCALIZATION: {
                "objective": "Scoprire in quale comune dell'Emilia-Romagna si trova il paziente.",
                "type": "open_text",  # ← TIPO FORZATO
                "example": "In quale comune ti trovi attualmente? (es: Bologna, Ravenna, Forlì)"
            },
            TriagePhase.CONSENT: {
                "objective": "Chiedere consenso esplicito per domande personali su salute mentale.",
                "type": "multiple_choice",  # ← TIPO FORZATO
                "example": "Se sei d'accordo, vorrei farti alcune domande personali per capire meglio come aiutarti.",
                "options_example": ["Sì, accetto", "Preferisco parlare con qualcuno direttamente"]
            },
            TriagePhase.FAST_TRIAGE: {
                "objective": f"Porre domanda {question_count+1} di 4 per valutare gravità emergenza. Focus: red flags, irradiazione dolore.",
                "type": "multiple_choice",  # ← TIPO FORZATO
                "example": "Il dolore al petto si irradia al braccio sinistro o alla mascella?",
                "options_example": ["Sì, al braccio sinistro", "Sì, alla mascella", "No", "Non sono sicuro/a"]
            },
            TriagePhase.PAIN_SCALE: {
                "objective": "Chiedere scala dolore 1-10 con descrizione chiara per ogni range.",
                "type": "multiple_choice",  # ← TIPO FORZATO
                "example": "Su una scala da 1 a 10, quanto è intenso il dolore che provi?",
                "options_example": ["1-3: Lieve (fastidio)", "4-6: Moderato (sopportabile)", "7-8: Forte (molto fastidioso)", "9-10: Insopportabile (peggiore immaginabile)"]
            },
            TriagePhase.DEMOGRAPHICS: {
                "objective": "Chiedere età del paziente (necessaria per raccomandazione struttura appropriata).",
                "type": "open_text",  # ← TIPO FORZATO (input numerico libero)
                "example": "Quanti anni hai?"
            },
            TriagePhase.CLINICAL_TRIAGE: {
                "objective": f"Porre domanda {question_count+1} di 5-7 per indagine clinica approfondita. Basati sui protocolli forniti.",
                "type": "multiple_choice",  # ← TIPO FORZATO (preferito per triage)
                "example": "Il dolore addominale che descrivi, quale di queste caratteristiche corrisponde meglio?",
                "options_example": ["Dolore acuto localizzato (crampo in un punto)", "Dolore diffuso costante (peso o gonfiore)", "Dolore intermittente (va e viene)"]
            },
            TriagePhase.RISK_ASSESSMENT: {
                "objective": f"Porre domanda {question_count+1} per valutare rischio autolesionismo/suicidio.",
                "type": "multiple_choice",  # ← TIPO FORZATO
                "example": "Negli ultimi giorni, hai avuto pensieri di farti del male?",
                "options_example": ["Mai", "Qualche volta", "Spesso", "Preferisco non rispondere"]
            }
        }
        
        config = phase_config.get(phase, {
            "objective": "Raccogliere informazioni cliniche.",
            "type": "open_text"
        })
        
        objective = config["objective"]
        required_type = config["type"]
        example_question = config.get("example", "")
        example_options = config.get("options_example", [])
        
        # Limiti domande per branch
        max_questions = {
            TriageBranch.EMERGENCY: 4,
            TriageBranch.MENTAL_HEALTH: 5,
            TriageBranch.STANDARD: 7
        }
        
        # Costruisci lista dati mancanti e già raccolti (MEMORIA ESPLICITA)
        missing_data = []
        known_data_text = []
        
        if phase == TriagePhase.CHIEF_COMPLAINT and "main_symptom" not in collected_data:
            missing_data.append("sintomo principale/motivo contatto")
        elif phase == TriagePhase.LOCALIZATION and not any(k in collected_data for k in ["location", "current_location"]):
            missing_data.append("località/comune")
        elif phase == TriagePhase.PAIN_SCALE and "pain_scale" not in collected_data:
            missing_data.append("scala dolore 1-10")
        elif phase == TriagePhase.DEMOGRAPHICS and "age" not in collected_data:
            missing_data.append("età paziente")
        
        for key, value in collected_data.items():
            if value:
                known_data_text.append(f"✅ {key}: {value}")
        
        prompt = f"""
SEI UN MEDICO ESPERTO IN TRIAGE TELEFONICO.

CONTESTO CONVERSAZIONE:
- Branch triage: {branch.value} ({branch.name})
- Fase corrente: {phase.value}
- Obiettivo fase: {objective}
- Domanda numero: {question_count + 1} (max {max_questions.get(branch, 7)})
- ⚠️ **TIPO DOMANDA OBBLIGATORIO**: {required_type.upper()}

📋 DATI GIÀ RACCOLTI (NON RICHIEDERE MAI QUESTI):
{chr(10).join(known_data_text) if known_data_text else "Nessun dato raccolto ancora."}

🎯 DATI MANCANTI DA RACCOGLIERE:
{', '.join(missing_data) if missing_data else "Tutti i dati base raccolti, procedi con indagine clinica."}

PROTOCOLLI CLINICI (da Knowledge Base):
{rag_context[:500] if rag_context else "Nessun protocollo specifico caricato."}

TASK:
Genera LA PROSSIMA SINGOLA DOMANDA da porre al paziente.

⚠️ REGOLE CRITICHE:
1. **TIPO DOMANDA VINCOLATO**: DEVI usare type="{required_type}"
2. **MEMORIA ASSOLUTA**: Se un dato è in "DATI GIÀ RACCOLTI", NON richiederlo MAI
3. **UNA SOLA DOMANDA** (Single Question Policy)
4. **Se type="open_text"**: NON generare opzioni (options: null)
5. **Se type="multiple_choice"**: DEVI generare 2-4 opzioni pertinenti
6. **MEDICALIZZA**: Se fase clinica, traduci sintomi in opzioni mediche

OUTPUT (JSON RIGOROSO):
{{
    "question": "Testo domanda esatta da porre al paziente",
    "type": "{required_type}",  ← DEVE CORRISPONDERE ESATTAMENTE
    "options": {{"null" if required_type == "open_text" else example_options}}
}}

ESEMPIO PER QUESTA FASE ({phase.value}):
{{
    "question": "{example_question}",
    "type": "{required_type}",
    "options": {{"null" if required_type == "open_text" else example_options}}
}}
"""
        
        return prompt
    
    def _generate_outcome_ai(self, branch: TriageBranch, collected_data: Dict) -> Dict:
        """
        Genera OUTCOME breve (2-4 righe) con raccomandazione struttura.
        SBAR completo viene generato in background ma non mostrato.
        """
        from datetime import datetime
        
        # 1. Trova struttura appropriata
        location = collected_data.get("location") or collected_data.get("current_location")
        recommendation = self._get_recommendation(branch, location, collected_data)
        
        # 2. Genera messaggio breve outcome
        prompt_outcome = f"""
        Genera un messaggio breve (MAX 3-4 righe) per concludere il triage.
        
        BRANCH: {branch.value}
        SINTOMO: {collected_data.get('chief_complaint') or collected_data.get('main_symptom', 'N/D')}
        DOLORE: {collected_data.get('pain_scale', 'N/D')}/10
        
        RACCOMANDAZIONE STRUTTURA:
        {recommendation}
        
        OUTPUT RICHIESTO:
        - 1-2 righe di sintesi ("Considerando i sintomi descritti...")
        - Raccomandazione struttura con emoji + nome + indirizzo + telefono + orari
        - Frase di chiusura empatica
        
        NON includere SBAR completo. NON elencare tutti i sintomi.
        Tono: professionale, rassicurante, conciso.
        
        ESEMPIO FORMATO:
        Considerando i sintomi descritti, ti consiglio di rivolgerti al:
        
        📍 **CAU Ravenna**
        Ospedale S. Maria delle Croci - Viale Randi, 5
        📞 0544 285111 | ⏰ Aperto 24/7
        
        Porta con te questo report quando ti recherai alla struttura.
        """
        
        try:
            if self.llm._groq_client:
                response = self.llm._groq_client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": prompt_outcome}],
                    temperature=0.3,
                    max_tokens=300
                )
                outcome_text = response.choices[0].message.content
            elif self.llm._gemini_model:
                response = self.llm._gemini_model.generate_content(prompt_outcome)
                outcome_text = response.text
            else:
                outcome_text = f"❌ Servizio AI non disponibile\n\n{recommendation}"
        except Exception as e:
            logger.error(f"❌ Errore generate_outcome_ai: {e}")
            outcome_text = f"{recommendation}\n\n(Report SBAR disponibile per download)"
        
        # 3. Genera SBAR completo in background (per download)
        sbar_data = self._generate_sbar_with_logs(branch, collected_data)
        
        # 4. Salva SBAR nello stato per permettere download
        self.state_manager.set(StateKeys.SBAR_REPORT_DATA, sbar_data)
        
        return {
            "text": outcome_text,
            "type": "outcome",  # ✅ Nuovo tipo (non "sbar_output")
            "options": None,
            "metadata": {
                "branch": branch.value,
                "complete": True,
                "sbar_available": True  # Flag per mostrare bottone download
            }
        }
    
    def _generate_sbar_with_logs(self, branch: TriageBranch, collected_data: Dict) -> Dict:
        """
        Genera report SBAR COMPLETO consultando triage_logs di Supabase.
        Questo metodo NON viene usato per chat output, solo per download PDF/TXT.
        """
        from datetime import datetime
        
        session_id = self.state_manager.get(StateKeys.SESSION_ID, "unknown")
        
        # ✅ 1. Recupera TUTTI i log della sessione da Supabase
        try:
            logs_response = self.db.supabase.table("triage_logs")\
                .select("*")\
                .eq("session_id", session_id)\
                .order("timestamp", desc=False)\
                .execute()
            
            all_logs = logs_response.data if logs_response.data else []
            logger.info(f"📊 Recuperati {len(all_logs)} log da Supabase per session {session_id}")
        except Exception as e:
            logger.error(f"❌ Errore fetch triage_logs: {e}")
            all_logs = []
        
        # ✅ 2. Estrai contesto conversazionale completo
        conversation_context = self._extract_conversation_context(all_logs)
        
        # ✅ 3. Trova struttura sanitaria
        location = collected_data.get("location") or collected_data.get("current_location")
        recommendation = self._get_recommendation(branch, location, collected_data)
        
        # ✅ 4. Genera SBAR usando TUTTI i dati
        prompt_sbar = f"""
        Genera report SBAR (Situation, Background, Assessment, Recommendation) COMPLETO.
        
        CONVERSAZIONE COMPLETA (da triage_logs):
        {conversation_context}
        
        DATI RACCOLTI (collected_data):
        {json.dumps(collected_data, indent=2, ensure_ascii=False)}
        
        BRANCH: {branch.value}
        RACCOMANDAZIONE STRUTTURA: {recommendation}
        
        OUTPUT RICHIESTO (formato SBAR standard):
        
        **S - SITUATION (Situazione)**
        [Sintomo principale + intensità dolore + insorgenza temporale]
        
        **B - BACKGROUND (Contesto)**
        [Età, genere, località, farmaci, patologie croniche, anamnesi rilevante]
        
        **A - ASSESSMENT (Valutazione)**
        [Triage {branch.value} completato, red flags rilevate, codice colore assegnato, numero domande poste]
        
        **R - RECOMMENDATION (Raccomandazione)**
        {recommendation}
        
        NOTA: Includi TUTTI i dettagli clinici raccolti durante la conversazione.
        """
        
        try:
            if self.llm._groq_client:
                response = self.llm._groq_client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": prompt_sbar}],
                    temperature=0.3,
                    max_tokens=800  # ← Aumentato per SBAR completo
                )
                sbar_text = response.choices[0].message.content
            elif self.llm._gemini_model:
                response = self.llm._gemini_model.generate_content(prompt_sbar)
                sbar_text = response.text
            else:
                sbar_text = "❌ Servizio AI non disponibile per generare SBAR"
        except Exception as e:
            logger.error(f"❌ Errore generate_sbar_with_logs: {e}")
            sbar_text = f"❌ Errore generazione SBAR: {str(e)}"
        
        return {
            "text": sbar_text,
            "timestamp": datetime.now().isoformat(),
            "session_id": session_id,
            "branch": branch.value,
            "collected_data": collected_data
        }
    
    def _extract_conversation_context(self, logs: list) -> str:
        """
        Estrae contesto conversazionale completo da triage_logs.
        Formato: timestamp, user_input, assistant_response.
        """
        if not logs:
            return "(Nessun log disponibile)"
        
        context_lines = []
        for i, log in enumerate(logs, 1):
            timestamp = log.get("timestamp", "")
            user_msg = log.get("user_input", "")
            bot_msg = log.get("assistant_response", "")
            
            context_lines.append(f"[{i}] {timestamp}")
            context_lines.append(f"User: {user_msg}")
            context_lines.append(f"Bot: {bot_msg[:200]}...")  # Tronca risposte lunghe
            context_lines.append("")  # Linea vuota
        
        return "\n".join(context_lines)
    
    def _get_recommendation(self, branch: TriageBranch, location: Optional[str], data: Dict) -> str:
        """Trova struttura sanitaria appropriata da master_kb.json."""
        
        if not location:
            return "⚠️ Località non specificata. Contatta il 118 per emergenze o il tuo medico di base."
        
        if branch == TriageBranch.EMERGENCY:
            facility = self.kb.find_healthcare_facility(location, "Pronto Soccorso")
            if facility:
                return f"""
📍 **{facility.get('nome', 'N/D')}**
📫 {facility.get('indirizzo', 'N/D')}
📞 {facility.get('telefono', 'N/D')}
🔗 [Monitora affollamento PS]({facility.get('link_monitoraggio', '#')})

⚠️ In caso di peggioramento: **chiama 118**
                """
        
        elif branch == TriageBranch.MENTAL_HEALTH:
            age = data.get("age", 99)
            facility_type = "Consultorio" if age and age < 18 else "CSM"
            facility = self.kb.find_healthcare_facility(location, facility_type)
            if facility:
                return f"""
📍 **{facility.get('nome', 'N/D')}**
📫 {facility.get('indirizzo', 'N/D')}
📞 {facility.get('telefono', 'N/D')}

**Numeri utili:**
🆘 Emergenza: 118
📞 Telefono Amico: 02 2327 2327
📞 Antiviolenza: 1522
                """
        
        else:  # Branch C
            facility = self.kb.find_healthcare_facility(location, "CAU")
            if facility:
                return f"""
📍 **{facility.get('nome', 'N/D')}**
📫 {facility.get('indirizzo', 'N/D')}
📞 {facility.get('telefono', 'N/D')}
⏰ {facility.get('orari', 'Contattare per orari')}
                """
        
        return "Consigliato consultare il medico di base o contattare il CUP regionale."
    
    def _get_urgency_level(self, branch: TriageBranch) -> int:
        """Mappa branch a urgency level."""
        mapping = {
            TriageBranch.EMERGENCY: 5,
            TriageBranch.MENTAL_HEALTH: 5,
            TriageBranch.STANDARD: 3,
            TriageBranch.INFO: 1
        }
        return mapping.get(branch, 3)


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_triage_controller: Optional[TriageController] = None


def get_triage_controller() -> TriageController:
    """Get singleton triage controller instance."""
    global _triage_controller
    if _triage_controller is None:
        _triage_controller = TriageController()
    return _triage_controller
