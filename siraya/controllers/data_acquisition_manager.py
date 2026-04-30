"""
DataAcquisitionManager — Distingue dati user-explicit da dati estratti/inferiti da LLM.

Regola fondamentale:
  - Dato user-explicit → collected (immediato)
  - Dato estratto/inferito da LLM → pending_validation + conferma pre-SBAR

Previene allucinazione di dati come età e posizione geografica.
"""

import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class DataAcquisitionManager:
    """
    Gestisce l'acquisizione consapevole dei dati del paziente.

    Distingue tra:
    - Dati esplicitamente forniti dall'utente (confirmed)
    - Dati estratti/inferiti dall'LLM (pending_validation)

    I dati in pending_validation richiedono conferma prima di essere inclusi nell'SBAR.
    """

    # Campi che richiedono conferma se non dichiarati esplicitamente
    SENSITIVE_FIELDS = {"location", "age", "gender"}

    # Pattern per rilevare dati esplicitamente dichiarati dall'utente
    EXPLICIT_LOCATION_PATTERNS = [
        r"(?:mi trovo|sono|abito|vivo|sono a)\s+a\s+([A-Z][a-zà-ù]+)",
        r"(?:città|comune|paese)\s+(?:di\s+)?([A-Z][a-zà-ù]+)",
        r"\bsono\s+di\s+([A-Z][a-zà-ù]+)",
        r"\ba\s+([A-Z][a-zà-ù]{3,})\b",
    ]

    EXPLICIT_AGE_PATTERNS = [
        r"\bho\s+(\d{1,3})\s+ann[io]",
        r"\bne\s+ho\s+(\d{1,3})\b",
        r"^\s*(\d{1,3})\s+ann[io]",
        r"\banni[:\s]+(\d{1,3})\b",
    ]

    def extract_and_validate(
        self,
        user_input: str,
        current_data: Dict[str, Any],
        llm_judge_output: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Analizza user_input e llm_judge_output, separando confirmed da pending.

        Args:
            user_input: Input dell'utente
            current_data: Dati già raccolti nella sessione
            llm_judge_output: Output del LLMJudge (può contenere dati estratti)

        Returns:
            {
                "confirmed": {campo: valore, ...},        # → va in collected direttamente
                "pending_validation": {campo: {           # → richiede conferma
                    "value": valore,
                    "source": "llm_extracted",
                    "question": "Domanda di conferma"
                }, ...},
                "validation_required": bool,              # True se ci sono dati pending
                "confirmation_questions": [str, ...]      # Domande di conferma da porre
            }
        """
        confirmed: Dict[str, Any] = {}
        pending_validation: Dict[str, Any] = {}
        confirmation_questions: List[str] = []

        import re

        user_lower = user_input.lower().strip()

        # ── Controlla location ──
        if "location" not in current_data:
            explicit_location = self._detect_explicit_location(user_input)
            if explicit_location:
                confirmed["location"] = explicit_location
                logger.info(f"✅ DataAcquisitionManager: location user-explicit='{explicit_location}'")
            elif llm_judge_output and llm_judge_output.get("extracted_location"):
                llm_loc = str(llm_judge_output["extracted_location"]).title()
                pending_validation["location"] = {
                    "value": llm_loc,
                    "source": "llm_extracted",
                    "question": f"Ho capito che ti trovi a {llm_loc}, è corretto?"
                }
                confirmation_questions.append(f"Ho capito che ti trovi a {llm_loc}, è corretto?")
                logger.info(f"⏳ DataAcquisitionManager: location LLM-inferred='{llm_loc}' → pending")

        # ── Controlla age ──
        if "age" not in current_data:
            explicit_age = self._detect_explicit_age(user_input)
            if explicit_age is not None:
                confirmed["age"] = explicit_age
                logger.info(f"✅ DataAcquisitionManager: age user-explicit={explicit_age}")
            elif llm_judge_output and llm_judge_output.get("extracted_age"):
                age_val = llm_judge_output["extracted_age"]
                if isinstance(age_val, (int, float)) and 0 < age_val < 120:
                    age_int = int(age_val)
                    pending_validation["age"] = {
                        "value": age_int,
                        "source": "llm_extracted",
                        "question": f"Ho capito che hai {age_int} anni, è corretto?"
                    }
                    confirmation_questions.append(f"Ho capito che hai {age_int} anni, è corretto?")
                    logger.info(f"⏳ DataAcquisitionManager: age LLM-inferred={age_int} → pending")

        validation_required = len(pending_validation) > 0

        return {
            "confirmed": confirmed,
            "pending_validation": pending_validation,
            "validation_required": validation_required,
            "confirmation_questions": confirmation_questions,
        }

    def process_validation_response(
        self,
        user_input: str,
        pending_validation: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Processa la risposta dell'utente alle domande di conferma.

        Args:
            user_input: Risposta dell'utente
            pending_validation: Dati in attesa di validazione

        Returns:
            {
                "confirmed": {campo: valore, ...},    # Dati confermati
                "rejected": [campo, ...],              # Dati rifiutati
                "still_pending": {campo: ..., ...}     # Dati ancora non risposti
            }
        """
        user_lower = user_input.lower().strip()
        confirmed: Dict[str, Any] = {}
        rejected: List[str] = []

        positive_words = ["sì", "si", "yes", "esatto", "corretto", "giusto", "ok", "confermo", "affermativo"]
        negative_words = ["no", "non", "sbagliato", "errato", "falso", "negativo"]

        is_positive = any(w in user_lower for w in positive_words)
        is_negative = any(w in user_lower for w in negative_words)

        for field, data in pending_validation.items():
            if is_positive:
                confirmed[field] = data["value"]
                logger.info(f"✅ DataAcquisitionManager: {field}='{data['value']}' CONFERMATO dall'utente")
            elif is_negative:
                rejected.append(field)
                logger.info(f"❌ DataAcquisitionManager: {field} RIFIUTATO dall'utente")

        # Campi non ancora risposti
        still_pending = {
            f: d for f, d in pending_validation.items()
            if f not in confirmed and f not in rejected
        }

        return {
            "confirmed": confirmed,
            "rejected": rejected,
            "still_pending": still_pending,
        }

    def generate_validation_message(self, pending_validation: Dict[str, Any]) -> str:
        """
        Genera il messaggio di conferma per i dati in pending.

        Args:
            pending_validation: Dizionario dei dati da confermare

        Returns:
            Messaggio da mostrare all'utente
        """
        if not pending_validation:
            return ""

        questions = [data["question"] for data in pending_validation.values()]

        if len(questions) == 1:
            return questions[0]

        intro = "Prima di procedere, ho bisogno di confermare alcune informazioni:\n"
        return intro + "\n".join(f"• {q}" for q in questions)

    def _detect_explicit_location(self, user_input: str) -> Optional[str]:
        """
        Rileva se la location è esplicitamente dichiarata dall'utente (non solo menzionata).
        """
        import re

        comuni_er = [
            "bologna", "modena", "parma", "reggio emilia", "piacenza",
            "ferrara", "ravenna", "forlì", "forli", "cesena", "rimini",
            "imola", "faenza", "lugo", "cervia", "riccione", "cattolica",
            "misano", "santarcangelo", "bellaria", "carpi", "casalecchio",
            "fidenza", "salsomaggiore", "sassuolo", "vignola", "castelfranco",
            "cento", "comacchio", "argenta", "budrio", "san giovanni in persiceto",
        ]

        # Pattern espliciti (l'utente dichiara attivamente la propria posizione)
        explicit_patterns = [
            r"(?:mi trovo|sono|abito|vivo)\s+a\s+([a-zà-ù\s]+?)(?:\s|$|,|\.)",
            r"(?:sono)\s+di\s+([a-zà-ù\s]+?)(?:\s|$|,|\.)",
            r"(?:città|comune|paese)[:\s]+([a-zà-ù\s]+?)(?:\s|$|,|\.)",
        ]

        text_lower = user_input.lower()

        for pattern in explicit_patterns:
            match = re.search(pattern, text_lower)
            if match:
                candidate = match.group(1).strip()
                for comune in comuni_er:
                    if comune in candidate or candidate in comune:
                        return comune.title()

        return None

    def _detect_explicit_age(self, user_input: str) -> Optional[int]:
        """
        Rileva se l'età è esplicitamente dichiarata dall'utente.
        """
        import re

        text_lower = user_input.lower().strip()

        explicit_age_patterns = [
            r"\bho\s+(\d{1,3})\s+ann[io]",
            r"\bne\s+ho\s+(\d{1,3})\b",
            r"^\s*(\d{1,3})\s+ann[io]",
            r"\banni[:\s]+(\d{1,3})\b",
            r"\b(\d{1,3})\s+anni\b",
        ]

        for pattern in explicit_age_patterns:
            match = re.search(pattern, text_lower)
            if match:
                try:
                    age = int(match.group(1))
                    if 0 < age < 120:
                        return age
                except (ValueError, IndexError):
                    pass

        return None
