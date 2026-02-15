"""
SIRAYA Triage Controller - AI-Driven Orchestrator
V2.0: Single Question Policy + Zero Hardcoded Questions
"""

from enum import Enum
from typing import Dict, Optional, Tuple
import time
import logging
import json

logger = logging.getLogger(__name__)


class TriageBranch(Enum):
    EMERGENCY = "A"          # Red/Orange
    MENTAL_HEALTH = "B"      # Black
    STANDARD = "C"           # Green/Yellow
    INFO = "INFO"            # Richieste informative


class TriagePhase(Enum):
    INTAKE = "intake"
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
        from ..core.state_manager import get_state_manager, StateKeys
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
        from ..core.state_manager import StateKeys
        
        current_branch = self.state_manager.get(StateKeys.TRIAGE_PATH)
        current_phase = self.state_manager.get(StateKeys.CURRENT_PHASE, TriagePhase.INTAKE.value)
        collected_data = self.state_manager.get(StateKeys.COLLECTED_DATA, {})
        question_count = self.state_manager.get(StateKeys.QUESTION_COUNT, 0)
        session_id = self.state_manager.get(StateKeys.SESSION_ID, "unknown")
        
        # 2. Prima interazione: classifica branch
        if not current_branch:
            current_branch = self._classify_branch(user_input)
            self.state_manager.set(StateKeys.TRIAGE_PATH, current_branch.value)
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
        
        return {
            "assistant_response": next_question["text"],
            "question_type": next_question["type"],
            "options": next_question.get("options"),
            "metadata": next_question.get("metadata", {}),
            "processing_time_ms": processing_time
        }
    
    def _classify_branch(self, user_input: str) -> TriageBranch:
        """Classifica intent in Branch A/B/C/INFO tramite keyword + AI fallback."""
        user_lower = user_input.lower()
        
        # Quick keyword matching per emergenze
        if any(kw in user_lower for kw in self.emergency_keywords):
            return TriageBranch.EMERGENCY
        
        if any(kw in user_lower for kw in self.mental_health_keywords):
            return TriageBranch.MENTAL_HEALTH
        
        # Check richieste informative
        if any(kw in user_lower for kw in self.info_keywords):
            return TriageBranch.INFO
        
        # Fallback: chiedi all'AI
        prompt = f"""
        Classifica questo messaggio in UNA delle 4 categorie:
        
        Input utente: "{user_input}"
        
        Categorie:
        - EMERGENCY: Sintomi gravi (dolore toracico, emorragia, trauma, difficoltà respiratorie)
        - MENTAL_HEALTH: Crisi psichiatrica, rischio autolesionismo, depressione grave
        - STANDARD: Sintomi non urgenti (mal di testa, dolori addominali, febbre)
        - INFO: Richieste informative su servizi sanitari
        
        Rispondi SOLO con: EMERGENCY, MENTAL_HEALTH, STANDARD o INFO
        """
        
        try:
            response = self.llm.generate_with_json_parse(prompt, temperature=0.0, max_tokens=10)
            if isinstance(response, dict) and "classification" in response:
                classification = response["classification"].strip().upper()
            else:
                # Se response è stringa
                classification = str(response).strip().upper()
            
            if classification in ["EMERGENCY", "MENTAL_HEALTH", "STANDARD", "INFO"]:
                return TriageBranch[classification]
        except Exception as e:
            logger.error(f"❌ Errore classify_branch AI: {e}")
        
        return TriageBranch.STANDARD  # Default sicuro
    
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
        from ..core.state_manager import StateKeys
        
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
        if branch == TriageBranch.STANDARD:
            # FASE 1: Localizzazione
            if current_phase == TriagePhase.INTAKE:
                # Se località già nota (da memoria Supabase), salta direttamente a pain_scale
                if "location" in collected_data or "current_location" in collected_data:
                    logger.info("✅ Località già nota, salto LOCALIZATION")
                    return TriagePhase.PAIN_SCALE
                return TriagePhase.LOCALIZATION
            
            # FASE 2: Pain Scale
            if current_phase == TriagePhase.LOCALIZATION:
                # FORCE ADVANCE: Se location presente, vai avanti
                if "location" in collected_data or "current_location" in collected_data:
                    return TriagePhase.PAIN_SCALE
                return TriagePhase.LOCALIZATION  # Rimani solo se manca
            
            if current_phase == TriagePhase.PAIN_SCALE:
                # FORCE ADVANCE: Se pain_scale presente, vai avanti
                if "pain_scale" in collected_data:
                    logger.info(f"✅ Scala dolore raccolta: {collected_data['pain_scale']}, avanzo a DEMOGRAPHICS")
                    return TriagePhase.DEMOGRAPHICS
                return TriagePhase.PAIN_SCALE  # Rimani solo se manca
            
            # FASE 3: Demographics
            if current_phase == TriagePhase.DEMOGRAPHICS:
                # FORCE ADVANCE: Se età presente, vai a clinical triage
                if "age" in collected_data:
                    logger.info(f"✅ Età raccolta: {collected_data['age']}, avanzo a CLINICAL_TRIAGE")
                    return TriagePhase.CLINICAL_TRIAGE
                return TriagePhase.DEMOGRAPHICS
            
            # FASE 4: Clinical Triage (5-7 domande)
            if current_phase == TriagePhase.CLINICAL_TRIAGE:
                # Avanza a SBAR dopo 5-7 domande O se dati sufficienti
                if question_count >= 5:
                    # Verifica dati minimi per SBAR
                    required_data = ["main_symptom", "pain_scale", "age"]
                    has_location = "location" in collected_data or "current_location" in collected_data
                    has_required = all(key in collected_data for key in required_data)
                    
                    if has_location and has_required:
                        logger.info(f"✅ {question_count} domande + dati completi, genero SBAR")
                        return TriagePhase.SBAR_GENERATION
                
                # Continua clinical triage fino a max 7 domande
                if question_count < 7:
                    return TriagePhase.CLINICAL_TRIAGE
                
                # Forza SBAR dopo 7 domande (anche se dati incompleti)
                logger.warning(f"⚠️ Max domande raggiunto ({question_count}), forzo SBAR")
                return TriagePhase.SBAR_GENERATION
            
            # Fallback: Se siamo già in SBAR, rimani
            if current_phase == TriagePhase.SBAR_GENERATION:
                return TriagePhase.SBAR_GENERATION
        
        # Branch A: Emergency (simile logica)
        if branch == TriageBranch.EMERGENCY:
            if current_phase == TriagePhase.INTAKE:
                if "location" in collected_data or "current_location" in collected_data:
                    return TriagePhase.FAST_TRIAGE
                return TriagePhase.LOCALIZATION
            
            if current_phase == TriagePhase.LOCALIZATION:
                if "location" in collected_data or "current_location" in collected_data:
                    return TriagePhase.FAST_TRIAGE
                return TriagePhase.LOCALIZATION
            
            if current_phase == TriagePhase.FAST_TRIAGE:
                if question_count >= 3:  # Min 3 domande per emergenza
                    return TriagePhase.SBAR_GENERATION
                return TriagePhase.FAST_TRIAGE
            
            if current_phase == TriagePhase.SBAR_GENERATION:
                return TriagePhase.SBAR_GENERATION
        
        # Branch B: Mental Health (simile logica)
        if branch == TriageBranch.MENTAL_HEALTH:
            if current_phase == TriagePhase.INTAKE:
                return TriagePhase.CONSENT
            
            if current_phase == TriagePhase.CONSENT:
                if collected_data.get("consent") == "yes":
                    return TriagePhase.DEMOGRAPHICS
                return TriagePhase.SBAR_GENERATION  # Se rifiuta consenso, vai a SBAR (con hotline)
            
            if current_phase == TriagePhase.DEMOGRAPHICS:
                if "age" in collected_data:
                    return TriagePhase.RISK_ASSESSMENT
                return TriagePhase.DEMOGRAPHICS
            
            if current_phase == TriagePhase.RISK_ASSESSMENT:
                if question_count >= 4:
                    return TriagePhase.SBAR_GENERATION
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
        
        # Recupera contesto RAG se fase clinica
        rag_context = ""
        if phase in [TriagePhase.FAST_TRIAGE, TriagePhase.CLINICAL_TRIAGE, TriagePhase.RISK_ASSESSMENT]:
            try:
                rag_docs = self.rag.retrieve_context(
                    query=collected_data.get("main_symptom", user_input),
                    k=3
                )
                rag_context = "\n".join([doc.get("content", "") for doc in rag_docs])
            except Exception as e:
                logger.warning(f"⚠️ RAG context retrieval failed: {e}")
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
            
            return {
                "text": response.get("question", "Puoi dirmi di più sui tuoi sintomi?"),
                "type": response.get("type", "open_text"),
                "options": response.get("options"),
                "metadata": {"ai_generated": True, "phase": phase.value}
            }
        except Exception as e:
            logger.error(f"❌ Errore generate_question_ai: {e}")
            # Fallback sicuro
            return {
                "text": "Puoi dirmi di più sui tuoi sintomi?",
                "type": "open_text",
                "options": None,
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
        """Costruisce prompt per AI che genera la domanda."""
        
        # Definisci obiettivo fase
        phase_objectives = {
            TriagePhase.LOCALIZATION: "Scoprire in quale comune dell'Emilia-Romagna si trova il paziente (per consigliare struttura sanitaria appropriata).",
            TriagePhase.CONSENT: "Chiedere consenso esplicito per domande personali su salute mentale. OPZIONI: 'Sì, accetto' / 'Preferisco parlare con qualcuno direttamente'",
            TriagePhase.FAST_TRIAGE: f"Porre domanda {question_count+1} di 4 per valutare gravità emergenza. Focus: red flags, irradiazione dolore, difficoltà respiratorie, perdita coscienza.",
            TriagePhase.PAIN_SCALE: "Chiedere scala dolore 1-10 con descrizione (1=fastidio lieve, 10=peggiore dolore immaginabile).",
            TriagePhase.DEMOGRAPHICS: "Chiedere età (necessaria per raccomandazione struttura appropriata: pediatria, adulti, geriatria).",
            TriagePhase.CLINICAL_TRIAGE: f"Porre domanda {question_count+1} di 5-7 per indagine clinica approfondita. Basati sui protocolli forniti.",
            TriagePhase.RISK_ASSESSMENT: f"Porre domanda {question_count+1} per valutare rischio autolesionismo/suicidio. Usa protocolli Columbia-Suicide o equivalenti."
        }
        
        objective = phase_objectives.get(phase, "Raccogliere informazioni cliniche.")
        
        # Limiti domande per branch
        max_questions = {
            TriageBranch.EMERGENCY: 4,
            TriageBranch.MENTAL_HEALTH: 5,
            TriageBranch.STANDARD: 7
        }
        
        # Costruisci lista dati mancanti e già raccolti (MEMORIA ESPLICITA)
        missing_data = []
        known_data_text = []
        
        if phase == TriagePhase.LOCALIZATION and not any(k in collected_data for k in ["location", "current_location"]):
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

📋 DATI GIÀ RACCOLTI (NON RICHIEDERE MAI QUESTI):
{chr(10).join(known_data_text) if known_data_text else "Nessun dato raccolto ancora."}

🎯 DATI MANCANTI DA RACCOGLIERE:
{', '.join(missing_data) if missing_data else "Tutti i dati base raccolti, procedi con indagine clinica."}

PROTOCOLLI CLINICI (da Knowledge Base):
{rag_context[:500] if rag_context else "Nessun protocollo specifico caricato."}

TASK:
Genera LA PROSSIMA SINGOLA DOMANDA da porre al paziente.

⚠️ REGOLE CRITICHE:
1. **MEMORIA ASSOLUTA**: Se un dato è in "DATI GIÀ RACCOLTI", NON richiederlo MAI
2. **UNA SOLA DOMANDA** (Single Question Policy)
3. **PREFERISCI MULTIPLE CHOICE**: Usa type="multiple_choice" con 2-4 opzioni quando possibile (80% delle domande)
4. **MEDICALIZZA**: Se fase clinica, traduci sintomi in opzioni mediche A/B/C
5. **AVANZA**: Non ripetere domande su dati già noti

OUTPUT (JSON RIGOROSO):
{{
    "question": "Testo domanda esatta",
    "type": "multiple_choice",
    "options": ["Opzione A", "Opzione B", "Opzione C", "Opzione D"]
}}

ESEMPIO CORRETTO (Branch C, fase clinical_triage):
{{
    "question": "Il dolore addominale che descrivi, quale di queste caratteristiche corrisponde meglio?",
    "type": "multiple_choice",
    "options": [
        "Dolore acuto e localizzato (crampo in punto preciso)",
        "Dolore diffuso e costante (gonfiore, pesantezza)",
        "Dolore intermittente (va e viene, tipo colico)",
        "Dolore che peggiora con il movimento"
    ]
}}

ESEMPIO CORRETTO (Branch A, fase fast_triage):
{{
    "question": "Hai avuto nausea o vomito insieme al dolore?",
    "type": "multiple_choice",
    "options": ["Sì, nausea e vomito", "Solo nausea", "Solo vomito", "No, nessuno dei due"]
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
