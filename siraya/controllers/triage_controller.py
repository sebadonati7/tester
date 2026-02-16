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
    FAST_TRIAGE = "fast_triage"      # Branch A: 3-4 domande emergenza
    PAIN_SCALE = "pain_scale"
    DEMOGRAPHICS = "demographics"
    CLINICAL_TRIAGE = "clinical_triage"  # Branch C: 5-7 domande
    RISK_ASSESSMENT = "risk_assessment"  # Branch B: valutazione rischio
    SBAR_GENERATION = "sbar"


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
        CORE: Processa input e ritorna prossima domanda generata dall'AI.
        
        Returns:
            {
                "assistant_response": str,
                "question_type": "multiple_choice" | "open_text" | "sbar",
                "options": List[str] | None,
                "metadata": Dict,
                "processing_time_ms": int
            }
        """
        start_time = time.time()
        
        # 1. Recupera stato conversazione
        current_branch = self.state_manager.get(StateKeys.TRIAGE_PATH)
        current_phase = self.state_manager.get(StateKeys.CURRENT_PHASE, TriagePhase.INTAKE.value)
        collected_data = self.state_manager.get(StateKeys.COLLECTED_DATA, {})
        question_count = self.state_manager.get(StateKeys.QUESTION_COUNT, 0)
        session_id = self.state_manager.get(StateKeys.SESSION_ID, "unknown")
        
        # ✅ DEBUG: Log stato attuale
        logger.info(f"📍 Stato attuale: branch={current_branch}, phase={current_phase}, q_count={question_count}")
        
        # 2. Prima interazione: classifica branch
        if not current_branch:
            current_branch = self._classify_branch(user_input)
            self.state_manager.set(StateKeys.TRIAGE_PATH, current_branch.value)  # Legacy key (per compatibilità con vecchio codice)
            self.state_manager.set(StateKeys.TRIAGE_BRANCH, current_branch.value)  # New key (per nuovo codice)
            logger.info(f"✅ Branch classificato: {current_branch.value}")
        else:
            current_branch = TriageBranch(current_branch)
        
        # 3. Estrai dati dall'input (slot filling via AI)
        extracted = self._extract_data_ai(user_input, collected_data)
        collected_data.update(extracted)
        self.state_manager.set(StateKeys.COLLECTED_DATA, collected_data)
        
        # 4. Verifica memoria Supabase per evitare duplicazioni
        known_data = self._fetch_known_data_from_history()
        collected_data.update(known_data)
        
        # 5. Determina fase successiva (FSM semplice)
        next_phase = self._determine_next_phase(
            current_branch, 
            TriagePhase(current_phase), 
            collected_data,
            question_count
        )
        
        # ✅ DEBUG: Log transizione fase
        if next_phase.value != current_phase:
            logger.info(f"🔄 Transizione fase: {current_phase} → {next_phase.value}")
        else:
            logger.info(f"⏸️ Rimango in fase: {current_phase}")
        
        self.state_manager.set(StateKeys.CURRENT_PHASE, next_phase.value)
        
        # 6. Genera prossima domanda tramite AI
        next_question = self._generate_question_ai(
            branch=current_branch,
            phase=next_phase,
            collected_data=collected_data,
            question_count=question_count,
            user_input=user_input
        )
        
        # 7. Incrementa contatore solo se non è SBAR
        if next_phase != TriagePhase.SBAR_GENERATION:
            self.state_manager.set(StateKeys.QUESTION_COUNT, question_count + 1)
        
        # 8. Salva su Supabase
        processing_time = int((time.time() - start_time) * 1000)
        self.db.save_interaction(
            session_id=session_id,
            user_input=user_input,
            assistant_response=next_question["text"],
            processing_time_ms=processing_time,
            session_state={
                "triage_path": current_branch.value,
                "current_phase": next_phase.value,
                "collected_data": collected_data,
                "question_count": question_count + 1,
                "urgency_level": self._get_urgency_level(current_branch)
            },
            metadata=next_question.get("metadata", {})
        )
        
        # 9. Prepara e salva risposta nello state per UI
        result = {
            "assistant_response": next_question["text"],
            "question_type": next_question["type"],
            "options": next_question.get("options"),
            "metadata": next_question.get("metadata", {}),
            "processing_time_ms": processing_time
        }
        
        # Salva risposta nello state per chat_view.py
        self.state_manager.set(StateKeys.LAST_BOT_RESPONSE, result)
        
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
    
    def _extract_data_ai(self, user_input: str, current_data: Dict) -> Dict:
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
        question_count: int
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
                    # ✅ RESET COUNTER: Quando entriamo in CLINICAL_TRIAGE, resettiamo il contatore
                    # In questo modo contiamo solo le domande della fase clinica (5-7)
                    self.state_manager.set(StateKeys.QUESTION_COUNT, 0)
                    return TriagePhase.CLINICAL_TRIAGE
                return TriagePhase.DEMOGRAPHICS
            
            # FASE 5: Clinical Triage (5-7 domande basate su protocolli)
            if current_phase == TriagePhase.CLINICAL_TRIAGE:
                # ✅ FIX: question_count ora conta SOLO domande clinical triage (resettato sopra)
                # Quindi quando arriviamo qui con question_count=0, è la prima domanda clinical
                
                # Verifica dati minimi per SBAR
                required_data = ["main_symptom", "pain_scale", "age"]
                has_location = "location" in collected_data or "current_location" in collected_data
                has_required = all(key in collected_data for key in required_data)
                
                # ⚠️ REGOLA IMPORTANTE: Contare solo domande CLINICAL_TRIAGE
                # question_count dovrebbe essere tra 0-7 in questa fase
                
                # Avanza a SBAR se:
                # - Almeno 5 domande clinical E dati completi
                # - OPPURE max 7 domande clinical raggiunte
                if question_count >= 5 and has_location and has_required:
                    logger.info(f"✅ {question_count} domande clinical + dati completi, genero SBAR")
                    return TriagePhase.SBAR_GENERATION
                
                if question_count >= 7:
                    logger.warning(f"⚠️ Max domande clinical raggiunto ({question_count}), forzo SBAR")
                    return TriagePhase.SBAR_GENERATION
                
                # Continua clinical triage
                logger.info(f"⏸️ Clinical triage continua (domanda {question_count + 1}/5-7)")
                return TriagePhase.CLINICAL_TRIAGE
            
            # FASE 6: SBAR (report finale)
            if current_phase == TriagePhase.SBAR_GENERATION:
                return TriagePhase.SBAR_GENERATION
        
        # Branch A: Emergency
        if branch == TriageBranch.EMERGENCY:
            if current_phase == TriagePhase.INTAKE:
                if "location" in collected_data or "current_location" in collected_data:
                    # ✅ RESET COUNTER: Quando entriamo in FAST_TRIAGE, resettiamo
                    self.state_manager.set(StateKeys.QUESTION_COUNT, 0)
                    return TriagePhase.FAST_TRIAGE
                return TriagePhase.LOCALIZATION
            
            if current_phase == TriagePhase.LOCALIZATION:
                if "location" in collected_data or "current_location" in collected_data:
                    # ✅ RESET COUNTER
                    self.state_manager.set(StateKeys.QUESTION_COUNT, 0)
                    return TriagePhase.FAST_TRIAGE
                return TriagePhase.LOCALIZATION
            
            if current_phase == TriagePhase.FAST_TRIAGE:
                # ✅ FIX: Ora question_count conta solo domande FAST_TRIAGE (3-4)
                if question_count >= 3:  # Min 3 domande per emergenza
                    logger.info(f"✅ {question_count} domande fast-triage completate, genero SBAR")
                    return TriagePhase.SBAR_GENERATION
                logger.info(f"⏸️ Fast triage continua (domanda {question_count + 1}/3-4)")
                return TriagePhase.FAST_TRIAGE
            
            if current_phase == TriagePhase.SBAR_GENERATION:
                return TriagePhase.SBAR_GENERATION
        
        # Branch B: Mental Health
        if branch == TriageBranch.MENTAL_HEALTH:
            if current_phase == TriagePhase.INTAKE:
                return TriagePhase.CONSENT
            
            if current_phase == TriagePhase.CONSENT:
                if collected_data.get("consent") == "yes":
                    return TriagePhase.DEMOGRAPHICS
                return TriagePhase.SBAR_GENERATION  # Se rifiuta consenso, vai a SBAR (con hotline)
            
            if current_phase == TriagePhase.DEMOGRAPHICS:
                if "age" in collected_data:
                    # ✅ RESET COUNTER: Quando entriamo in RISK_ASSESSMENT, resettiamo
                    self.state_manager.set(StateKeys.QUESTION_COUNT, 0)
                    return TriagePhase.RISK_ASSESSMENT
                return TriagePhase.DEMOGRAPHICS
            
            if current_phase == TriagePhase.RISK_ASSESSMENT:
                # ✅ FIX: Ora question_count conta solo domande RISK_ASSESSMENT (4-5)
                if question_count >= 4:
                    logger.info(f"✅ {question_count} domande risk assessment completate, genero SBAR")
                    return TriagePhase.SBAR_GENERATION
                logger.info(f"⏸️ Risk assessment continua (domanda {question_count + 1}/4-5)")
                return TriagePhase.RISK_ASSESSMENT
            
            if current_phase == TriagePhase.SBAR_GENERATION:
                return TriagePhase.SBAR_GENERATION
        
        # Fallback sicuro
        logger.warning(f"⚠️ FSM fallback: branch={branch}, phase={current_phase}")
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
        
        # Se fase SBAR → genera report finale
        if phase == TriagePhase.SBAR_GENERATION:
            return self._generate_sbar_ai(branch, collected_data)
        
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
    
    def _generate_sbar_ai(self, branch: TriageBranch, collected_data: Dict) -> Dict:
        """Genera report SBAR finale tramite AI."""
        
        # Trova struttura sanitaria appropriata
        location = collected_data.get("location") or collected_data.get("current_location")
        recommendation = self._get_recommendation(branch, location, collected_data)
        
        prompt = f"""
        Genera report SBAR (Situation, Background, Assessment, Recommendation) per triage completato.
        
        BRANCH: {branch.value}
        DATI RACCOLTI:
        {json.dumps(collected_data, indent=2, ensure_ascii=False)}
        
        RACCOMANDAZIONE STRUTTURA:
        {recommendation}
        
        OUTPUT (formato SBAR):
        **REPORT TRIAGE SIRAYA**
        
        **S - SITUATION (Situazione)**
        [Sintomo principale + intensità dolore se presente]
        
        **B - BACKGROUND (Contesto)**
        [Età, genere, farmaci, patologie croniche]
        
        **A - ASSESSMENT (Valutazione)**
        [Triage {branch.value} completato, red flags rilevate, codice colore]
        
        **R - RECOMMENDATION (Raccomandazione)**
        {recommendation}
        """
        
        try:
            if self.llm._groq_client:
                response = self.llm._groq_client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=500
                )
                sbar_text = response.choices[0].message.content
            elif self.llm._gemini_model:
                response = self.llm._gemini_model.generate_content(prompt)
                sbar_text = response.text
            else:
                sbar_text = "❌ Servizio AI non disponibile per generare SBAR"
        except Exception as e:
            logger.error(f"❌ Errore generate_sbar_ai: {e}")
            sbar_text = f"❌ Errore generazione SBAR: {str(e)}"
        
        return {
            "text": sbar_text,
            "type": "sbar_output",
            "options": None,
            "metadata": {"branch": branch.value, "complete": True}
        }
    
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
