"""
ProtocolDrivenExecutor — Esegue percorsi BLACK basati su protocolli validati caricati da Supabase.

Flow:
1. Identifica concern type (suicidal_ideation | depression | eating_disorder | substance_abuse)
2. Carica protocollo chunckizzato da Supabase (tabella mental_health_protocols)
3. Passa chunk + conversation history a LLM
4. LLM genera prossima domanda seguendo il protocollo
5. Raccoglie risposte fino a completamento
6. Stratifica rischio secondo il protocollo
7. Raccomanda facility matching
"""

import logging
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class ProtocolDrivenExecutor:
    """
    Esegue percorsi BLACK basati su protocolli validati caricati da Supabase.

    Protocolli supportati:
    - ASQ (Ask Suicide Screening Questions) → suicidal_ideation
    - PHQ-9 (Patient Health Questionnaire-9) → depression
    - DCA (Disturbi del Comportamento Alimentare) → eating_disorder
    - SERT (Screening sostanze) → substance_abuse
    """

    # Keyword per identificare il concern type
    CONCERN_KEYWORDS = {
        "suicidal_ideation": [
            "voglio morire", "suicidio", "suicidarmi", "ammazzarmi", "farla finita",
            "non voglio più vivere", "togliermi la vita", "mi voglio uccidere",
            "piano per morire", "metodi per morire", "pillole per morire",
        ],
        "depression": [
            "depresso", "depressione", "triste da mesi", "tristezza profonda",
            "senza speranza", "niente ha senso", "non riesco ad alzarmi",
            "piango sempre", "vuoto interiore", "non provo emozioni",
            "ansia cronica", "stanchezza costante", "non dormo da settimane",
        ],
        "eating_disorder": [
            "non mangio", "vomito dopo mangiare", "mi faccio vomitare",
            "anoressia", "bulimia", "ho paura di ingrassare", "non riesco a mangiare",
            "mangio in modo compulsivo", "abbuffate", "purghe",
        ],
        "substance_abuse": [
            "cocaina", "eroina", "droga", "dipendenza", "astinenza",
            "droghe pesanti", "droghe leggere", "cannabis", "alcool dipendente",
            "non riesco a smettere di bere", "overdose",
        ],
    }

    # Risk stratification by concern type
    RISK_STRATIFICATION = {
        "suicidal_ideation": {
            "immediate": {
                "indicators": ["piano concreto", "metodo identificato", "data stabilita", "lettera di addio"],
                "facility": "SPDC",
                "action": "118",
                "rationale": "Ideazione suicidaria con piano concreto — rischio immediato",
            },
            "high": {
                "indicators": ["voglio morire", "pensieri ricorrenti", "senza piano"],
                "facility": "Crisis Center",
                "action": "118 se necessario",
                "rationale": "Ideazione suicidaria attiva senza piano — rischio elevato",
            },
            "moderate": {
                "indicators": ["pensieri passivi", "stanchezza di vivere", "non voglio svegliarmi"],
                "facility": "CSM",
                "action": None,
                "rationale": "Pensieri passivi di morte — rischio moderato",
            },
            "low": {
                "indicators": ["no pensieri", "solo tristezza"],
                "facility": "MMG",
                "action": None,
                "rationale": "Nessun pensiero attivo di morte — rischio basso",
            },
        },
        "depression": {
            "high": {
                "score_threshold": 20,
                "facility": "CSM",
                "urgency": "urgente",
                "rationale": "PHQ-9 score ≥ 20 — depressione grave",
            },
            "moderate": {
                "score_threshold": 10,
                "facility": "CSM",
                "urgency": "standard",
                "rationale": "PHQ-9 score 10-19 — depressione moderata",
            },
            "low": {
                "score_threshold": 0,
                "facility": "MMG",
                "urgency": "follow-up",
                "rationale": "PHQ-9 score < 10 — sintomi lievi",
            },
        },
        "eating_disorder": {
            "default": {
                "facility": "Centro DCA",
                "rationale": "Disturbo del comportamento alimentare — Centro specializzato DCA",
            }
        },
        "substance_abuse": {
            "default": {
                "facility": "SERT",
                "rationale": "Dipendenza da sostanze — Servizio tossicodipendenze",
            }
        },
    }

    # Facility contact templates
    FACILITY_INFO = {
        "SPDC": "Servizio Psichiatrico di Diagnosi e Cura — Pronto Soccorso Psichiatrico",
        "Crisis Center": "Centro di Crisi — accesso H24",
        "CSM": "Centro di Salute Mentale — prenotazione o accesso diretto urgente",
        "Centro DCA": "Centro per i Disturbi del Comportamento Alimentare",
        "SERT": "Servizio per le Dipendenze — SerD",
        "MMG": "Medico di Medicina Generale (medico di base)",
    }

    def __init__(self, rag_service, llm_service, supabase_client=None):
        """
        Args:
            rag_service: RAGService instance
            llm_service: LLMService instance
            supabase_client: Supabase client (optional, for direct DB access)
        """
        self.rag_service = rag_service
        self.llm_service = llm_service
        self.supabase = supabase_client

    def identify_concern_type(self, user_input: str, chief_complaint: str = "") -> str:
        """
        Identifica il tipo di problematica psicologica dal testo dell'utente.

        Returns:
            "suicidal_ideation" | "depression" | "eating_disorder" | "substance_abuse" | "general_mental_health"
        """
        combined = f"{user_input} {chief_complaint}".lower()

        # Suicidal ideation ha priorità massima
        for concern_type, keywords in self.CONCERN_KEYWORDS.items():
            if concern_type == "suicidal_ideation":
                if any(kw in combined for kw in keywords):
                    logger.info(f"🧠 ProtocolDrivenExecutor: concern_type=suicidal_ideation")
                    return "suicidal_ideation"

        # Poi gli altri
        for concern_type, keywords in self.CONCERN_KEYWORDS.items():
            if concern_type == "suicidal_ideation":
                continue
            if any(kw in combined for kw in keywords):
                logger.info(f"🧠 ProtocolDrivenExecutor: concern_type={concern_type}")
                return concern_type

        logger.info("🧠 ProtocolDrivenExecutor: concern_type=general_mental_health (default)")
        return "general_mental_health"

    def load_protocol_from_supabase(self, concern_type: str) -> Dict:
        """
        Carica protocollo chunckizzato da Supabase (tabella mental_health_protocols).

        Falls back to built-in protocol definitions if Supabase unavailable.

        Returns:
            {
                "protocol_name": str,
                "concern_type": str,
                "chunks": [{"chunk_order": int, "chunk_content": str, "metadata": dict}, ...],
                "risk_stratification_levels": dict
            }
        """
        if self.supabase:
            try:
                result = self.supabase.table("mental_health_protocols") \
                    .select("chunk_order, chunk_content, metadata, risk_stratification") \
                    .eq("protocol_type", concern_type) \
                    .order("chunk_order") \
                    .execute()

                if result.data:
                    logger.info(
                        f"✅ ProtocolDrivenExecutor: caricati {len(result.data)} chunks "
                        f"per '{concern_type}' da Supabase"
                    )
                    return {
                        "protocol_name": self._get_protocol_name(concern_type),
                        "concern_type": concern_type,
                        "chunks": result.data,
                        "risk_stratification_levels": self.RISK_STRATIFICATION.get(concern_type, {}),
                    }
            except Exception as e:
                logger.warning(f"⚠️ ProtocolDrivenExecutor: Supabase non disponibile: {e}")

        # Fallback: usa RAG service per recuperare protocolli
        return self._load_protocol_from_rag(concern_type)

    def execute_protocol(
        self,
        protocol_data: Dict,
        conversation_history: List[Dict],
    ) -> Dict:
        """
        Esegue il protocollo passo per passo, generando la prossima domanda via LLM.

        Args:
            protocol_data: Protocollo caricato da load_protocol_from_supabase()
            conversation_history: Lista di {"role": "user"|"assistant", "content": str}

        Returns:
            {
                "next_question": str,
                "protocol_progress": float,  # 0.0-1.0
                "current_chunk": int,
                "risk_indicators": list,
                "completed": bool,
                "protocol_conversation": list
            }
        """
        chunks = protocol_data.get("chunks", [])
        if not chunks:
            logger.warning("⚠️ ProtocolDrivenExecutor: nessun chunk disponibile")
            return self._generate_generic_mental_health_question(conversation_history)

        # Determina il chunk corrente in base alla conversazione
        user_turns = [m for m in conversation_history if m.get("role") == "user"]
        current_chunk_idx = min(len(user_turns), len(chunks) - 1)
        current_chunk = chunks[current_chunk_idx]

        # Calcola progresso
        progress = (current_chunk_idx + 1) / len(chunks)
        completed = current_chunk_idx >= len(chunks) - 1 and len(user_turns) >= len(chunks)

        # Raccoglie indicatori di rischio dalla conversazione
        risk_indicators = self._extract_risk_indicators(conversation_history)

        if completed:
            logger.info(f"✅ ProtocolDrivenExecutor: protocollo completato ({len(user_turns)} turni)")
            return {
                "next_question": "",
                "protocol_progress": 1.0,
                "current_chunk": current_chunk_idx,
                "risk_indicators": risk_indicators,
                "completed": True,
                "protocol_conversation": conversation_history,
            }

        # Genera prossima domanda tramite LLM
        chunk_content = current_chunk.get("chunk_content", "")
        next_question = self._generate_protocol_question(
            chunk_content=chunk_content,
            conversation_history=conversation_history,
            protocol_name=protocol_data.get("protocol_name", "Protocollo clinico"),
        )

        logger.info(
            f"🩺 ProtocolDrivenExecutor: chunk {current_chunk_idx + 1}/{len(chunks)}, "
            f"progress={progress:.0%}"
        )

        return {
            "next_question": next_question,
            "protocol_progress": progress,
            "current_chunk": current_chunk_idx,
            "risk_indicators": risk_indicators,
            "completed": False,
            "protocol_conversation": conversation_history,
        }

    def stratify_risk(
        self,
        protocol_data: Dict,
        responses_collected: Any,
        concern_type: str,
    ) -> Dict:
        """
        Stratifica il rischio in base al protocollo e alle risposte raccolte.

        Returns:
            {
                "risk_level": "immediate" | "high" | "moderate" | "low",
                "recommended_facility": str,
                "rationale": str,
                "urgent_action": bool,
                "recommended_actions": [str, ...]
            }
        """
        conversation = []
        if isinstance(responses_collected, list):
            conversation = responses_collected
        elif isinstance(responses_collected, dict):
            conversation = responses_collected.get("protocol_conversation", [])

        combined_responses = " ".join(
            m.get("content", "") for m in conversation if m.get("role") == "user"
        ).lower()

        # Suicidal ideation: usa indicatori clinici
        if concern_type == "suicidal_ideation":
            return self._stratify_suicidal_ideation(combined_responses)

        # Depression: stima score PHQ-9 dal testo
        if concern_type == "depression":
            return self._stratify_depression(combined_responses)

        # Eating disorder
        if concern_type == "eating_disorder":
            strat = self.RISK_STRATIFICATION["eating_disorder"]["default"]
            return {
                "risk_level": "moderate",
                "recommended_facility": strat["facility"],
                "rationale": strat["rationale"],
                "urgent_action": False,
                "recommended_actions": [],
            }

        # Substance abuse
        if concern_type == "substance_abuse":
            strat = self.RISK_STRATIFICATION["substance_abuse"]["default"]
            return {
                "risk_level": "moderate",
                "recommended_facility": strat["facility"],
                "rationale": strat["rationale"],
                "urgent_action": False,
                "recommended_actions": [],
            }

        # General mental health fallback
        return {
            "risk_level": "moderate",
            "recommended_facility": "CSM",
            "rationale": "Disagio psicologico generico — valutazione CSM consigliata",
            "urgent_action": False,
            "recommended_actions": [],
        }

    # =========================================================================
    # PRIVATE HELPERS
    # =========================================================================

    def _generate_protocol_question(
        self,
        chunk_content: str,
        conversation_history: List[Dict],
        protocol_name: str,
    ) -> str:
        """Genera prossima domanda con LLM usando il chunk del protocollo come contesto."""
        history_text = "\n".join([
            f"{'Paziente' if m['role'] == 'user' else 'Operatore'}: {m['content']}"
            for m in conversation_history[-6:]
        ])

        prompt = f"""Sei un operatore medico che segue il protocollo clinico validato:

{chunk_content}

Conversazione finora:
{history_text if history_text else "(Prima domanda del protocollo)"}

Genera la PROSSIMA domanda in base al protocollo {protocol_name}.
- Se il protocollo è concluso, indica SOLO: {{"completed": true}}
- Altrimenti, genera UNA domanda empatica, in italiano, che segua il protocollo
- Rispondi SOLO con JSON: {{"question": "La tua domanda qui?", "completed": false}}
- Tono: empatico, non giudicante, professionale"""

        try:
            result = self.llm_service.generate_with_json_parse(prompt, temperature=0.3, max_tokens=300)
            if result:
                if result.get("completed"):
                    return ""
                if result.get("question"):
                    return result["question"]
        except Exception as e:
            logger.error(f"❌ ProtocolDrivenExecutor: LLM error: {e}")

        # Fallback deterministico
        return self._extract_first_question_from_chunk(chunk_content)

    def _extract_first_question_from_chunk(self, chunk_content: str) -> str:
        """Estrae la prima domanda da un chunk di protocollo come fallback."""
        lines = chunk_content.strip().split("\n")
        for line in lines:
            line = line.strip()
            if line.endswith("?") and len(line) > 10:
                return line
        # Nessuna domanda trovata — domanda generica empatica
        return "Puoi raccontarmi come ti senti in questo momento?"

    def _generate_generic_mental_health_question(self, conversation_history: List[Dict]) -> Dict:
        """Genera una domanda generica per salute mentale se nessun protocollo è disponibile."""
        user_turns = len([m for m in conversation_history if m.get("role") == "user"])
        generic_questions = [
            "Puoi raccontarmi come ti senti in questo momento?",
            "Da quanto tempo provi questi sentimenti?",
            "C'è qualcosa di specifico che ha scatenato questo stato d'animo?",
            "Come stai dormendo e mangiando in questi giorni?",
        ]
        question = generic_questions[min(user_turns, len(generic_questions) - 1)]
        completed = user_turns >= len(generic_questions)

        return {
            "next_question": question,
            "protocol_progress": (user_turns + 1) / len(generic_questions),
            "current_chunk": user_turns,
            "risk_indicators": self._extract_risk_indicators(conversation_history),
            "completed": completed,
            "protocol_conversation": conversation_history,
        }

    def _extract_risk_indicators(self, conversation_history: List[Dict]) -> List[str]:
        """Estrae indicatori di rischio dalla conversazione."""
        combined = " ".join(
            m.get("content", "") for m in conversation_history if m.get("role") == "user"
        ).lower()

        high_risk_phrases = [
            "piano", "metodo", "corda", "pillole", "arma",
            "domani", "stanotte", "stasera farla finita",
            "lettera", "testamento", "addio",
        ]
        indicators = [p for p in high_risk_phrases if p in combined]
        return indicators

    def _stratify_suicidal_ideation(self, combined_responses: str) -> Dict:
        """Stratifica rischio per ideazione suicidaria (protocollo ASQ)."""
        strat_config = self.RISK_STRATIFICATION["suicidal_ideation"]

        # IMMEDIATE: piano concreto
        immediate_indicators = strat_config["immediate"]["indicators"]
        if any(ind in combined_responses for ind in immediate_indicators):
            return {
                "risk_level": "immediate",
                "recommended_facility": strat_config["immediate"]["facility"],
                "rationale": strat_config["immediate"]["rationale"],
                "urgent_action": True,
                "recommended_actions": ["118"],
            }

        # HIGH: pensieri attivi senza piano
        high_keywords = ["voglio morire", "voglio morire", "non voglio più vivere", "farla finita"]
        if any(kw in combined_responses for kw in high_keywords):
            return {
                "risk_level": "high",
                "recommended_facility": strat_config["high"]["facility"],
                "rationale": strat_config["high"]["rationale"],
                "urgent_action": True,
                "recommended_actions": ["118 se necessario"],
            }

        # MODERATE: pensieri passivi
        moderate_keywords = ["stanco di vivere", "non voglio svegliarmi", "meglio non esserci"]
        if any(kw in combined_responses for kw in moderate_keywords):
            return {
                "risk_level": "moderate",
                "recommended_facility": strat_config["moderate"]["facility"],
                "rationale": strat_config["moderate"]["rationale"],
                "urgent_action": False,
                "recommended_actions": [],
            }

        # LOW
        return {
            "risk_level": "low",
            "recommended_facility": strat_config["low"]["facility"],
            "rationale": strat_config["low"]["rationale"],
            "urgent_action": False,
            "recommended_actions": [],
        }

    def _stratify_depression(self, combined_responses: str) -> Dict:
        """Stratifica rischio per depressione (stima PHQ-9 dal testo)."""
        strat_config = self.RISK_STRATIFICATION["depression"]

        # Stima semplificata del punteggio PHQ-9 da keyword
        score = 0
        phq9_indicators = [
            ("triste", 2), ("vuoto", 2), ("senza speranza", 3),
            ("non dormo", 2), ("dormo troppo", 1), ("stanco", 2),
            ("non mangio", 2), ("mangio troppo", 1),
            ("non mi concentro", 2), ("inutile", 3), ("colpa", 2),
            ("lento", 1), ("agitato", 1), ("non riesco ad alzarmi", 3),
        ]
        for kw, weight in phq9_indicators:
            if kw in combined_responses:
                score += weight

        if score >= 20:
            return {
                "risk_level": "high",
                "recommended_facility": strat_config["high"]["facility"],
                "rationale": f"{strat_config['high']['rationale']} (score stimato: {score})",
                "urgent_action": False,
                "recommended_actions": [],
            }
        if score >= 10:
            return {
                "risk_level": "moderate",
                "recommended_facility": strat_config["moderate"]["facility"],
                "rationale": f"{strat_config['moderate']['rationale']} (score stimato: {score})",
                "urgent_action": False,
                "recommended_actions": [],
            }
        return {
            "risk_level": "low",
            "recommended_facility": strat_config["low"]["facility"],
            "rationale": f"{strat_config['low']['rationale']} (score stimato: {score})",
            "urgent_action": False,
            "recommended_actions": [],
        }

    def _load_protocol_from_rag(self, concern_type: str) -> Dict:
        """Carica protocollo da RAG service come fallback quando Supabase non disponibile."""
        protocol_name = self._get_protocol_name(concern_type)

        # Tenta recupero via RAG
        try:
            chunks = self.rag_service.retrieve_context(concern_type, k=5)
            if chunks:
                supabase_chunks = [
                    {
                        "chunk_order": i,
                        "chunk_content": c.get("content", ""),
                        "metadata": {"source": c.get("source", "")},
                    }
                    for i, c in enumerate(chunks)
                ]
                logger.info(
                    f"✅ ProtocolDrivenExecutor: {len(supabase_chunks)} chunks da RAG per '{concern_type}'"
                )
                return {
                    "protocol_name": protocol_name,
                    "concern_type": concern_type,
                    "chunks": supabase_chunks,
                    "risk_stratification_levels": self.RISK_STRATIFICATION.get(concern_type, {}),
                }
        except Exception as e:
            logger.warning(f"⚠️ RAG fallback failed: {e}")

        # Ultimo fallback: protocollo embedded minimale
        return self._get_embedded_protocol(concern_type)

    def _get_embedded_protocol(self, concern_type: str) -> Dict:
        """Restituisce un protocollo minimale embedded come ultimo fallback."""
        embedded_protocols = {
            "suicidal_ideation": [
                "Hai avuto pensieri di farti del male o di toglierti la vita?",
                "Hai un piano su come farlo?",
                "Hai accesso ai mezzi per attuare questo piano?",
                "Hai già tentato di toglierti la vita in passato?",
                "Hai qualcuno con cui puoi parlare in questo momento?",
            ],
            "depression": [
                "Da quanto tempo ti senti così?",
                "Come descrivi il tuo umore nelle ultime due settimane?",
                "Riesci a dormire? Hai cambiamenti nell'appetito?",
                "Riesci a svolgere le attività quotidiane?",
                "Hai pensieri negativi su te stesso o sul futuro?",
            ],
            "eating_disorder": [
                "Puoi descrivermi il tuo rapporto con il cibo in questo periodo?",
                "Hai cambiamenti significativi nel peso corporeo recentemente?",
                "Come ti senti rispetto al tuo corpo?",
                "Hai mai vomitato intenzionalmente dopo aver mangiato?",
            ],
            "substance_abuse": [
                "Quali sostanze stai assumendo e con quale frequenza?",
                "Da quanto tempo fai uso di queste sostanze?",
                "Hai già provato a smettere? Cosa è successo?",
                "L'uso di sostanze interferisce con la tua vita quotidiana?",
            ],
            "general_mental_health": [
                "Puoi raccontarmi come ti senti in questo momento?",
                "Da quanto tempo provi questi sentimenti?",
                "C'è qualcosa di specifico che ha scatenato questo stato d'animo?",
                "Come stai dormendo e mangiando in questi giorni?",
            ],
        }

        questions = embedded_protocols.get(concern_type, embedded_protocols["general_mental_health"])
        chunks = [
            {"chunk_order": i, "chunk_content": q, "metadata": {"source": "embedded"}}
            for i, q in enumerate(questions)
        ]

        return {
            "protocol_name": self._get_protocol_name(concern_type),
            "concern_type": concern_type,
            "chunks": chunks,
            "risk_stratification_levels": self.RISK_STRATIFICATION.get(concern_type, {}),
        }

    def _get_protocol_name(self, concern_type: str) -> str:
        """Restituisce il nome esteso del protocollo per concern_type."""
        names = {
            "suicidal_ideation": "ASQ - Ask Suicide Screening Questions",
            "depression": "PHQ-9 - Patient Health Questionnaire-9",
            "eating_disorder": "DCA - Disturbi del Comportamento Alimentare",
            "substance_abuse": "SERT - Screening Dipendenze da Sostanze",
            "general_mental_health": "Protocollo Salute Mentale Generale",
        }
        return names.get(concern_type, "Protocollo Salute Mentale")
