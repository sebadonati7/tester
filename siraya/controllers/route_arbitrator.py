"""
RouteArbitrator — Routing dinamico per SIRAYA.

Permette switch verso INFO in qualsiasi turno della conversazione
e gestisce la classificazione del branch al primo turno.
"""

import logging
import re
from typing import Tuple, Optional, Dict

logger = logging.getLogger(__name__)


class RouteArbitrator:
    """
    Routing dinamico: permette switch verso INFO in qualsiasi turno.

    Responsabilità:
    - Primo turno: classifica branch usando urgency_override (pre-calcolato
      da LLMJudge nel slot filler) con SmartRouter come safety net.
    - Turni successivi: controlla se è query INFO; gestisce escalation C→A.
    """

    INFO_PATTERNS = [
        r"\borari\s+(di|del|della|delle)\b",
        r"\bquando\s+(apre|chiude|è\s+aperto)\b",
        r"\b(numero|telefono|email)\s+(di|del|della)\b",
        r"\bcontatti\s+(di|del)\b",
        r"\b(indirizzo|dove|ubicazione)\s+(di|del|della)\b",
        r"\bdove\s+(si\s+trova|posso\s+trovare)\b",
        r"\bcosa\s+offre\b",
        r"\bquali\s+servizi\b",
        r"\binformazioni\s+su\b",
        r"\binfo\s+su\b",
        r"\bcome\s+(prenot\w*|registr\w*)\b",
        r"\bprenotazione\b",
    ]

    EMERGENCY_KEYWORDS = [
        "cuore", "petto", "respiro", "svengo", "collasso",
        "sangue", "emorragia", "grave", "urgente",
    ]

    def __init__(self, llm_service, llm_judge):
        """
        Args:
            llm_service: Istanza di LLMService (usata come fallback per primo turno).
            llm_judge: Classe LLMJudge (usata per evaluate_input al primo turno).
        """
        self.llm_service = llm_service
        self.llm_judge = llm_judge

    def route(
        self,
        user_input: str,
        current_data: Dict,
        current_branch: Optional[str],
        current_phase: str,
        urgency_override: Optional[str] = None,
    ) -> Tuple[str, float, float]:
        """
        Route dinamico.

        Args:
            user_input: Input utente corrente.
            current_data: Dati raccolti finora.
            current_branch: Branch attivo (None al primo turno).
            current_phase: Fase FSM corrente.
            urgency_override: Hint già calcolato da LLMJudge nel slot filler
                              ("emergency", "mental_health", "info" o None).

        Returns:
            Tuple (branch_str, urgency_score, confidence)
        """
        # Primo turno (branch non ancora assegnato)
        if current_branch is None:
            return self._route_first_turn(user_input, urgency_override)

        # Turni successivi: controlla switch dinamico a INFO
        if self._is_info_query(user_input):
            logger.info(f"RouteArbitrator: switch dinamico → INFO ('{user_input[:50]}')")
            return ("INFO", 0.0, 0.95)

        # Escalation C → A durante percorso standard
        if current_branch == "C":
            from .smart_router import SmartRouter
            if SmartRouter.check_escalation(user_input):
                logger.warning(f"RouteArbitrator: escalation C→A ('{user_input[:50]}')")
                return ("A", 3.0, 0.9)

        # Mantieni branch attuale
        if current_branch in ("A", "B", "C", "INFO"):
            return (current_branch, self._compute_urgency(user_input), 0.7)

        # Fallback: riclassifica
        return self._route_first_turn(user_input, urgency_override)

    def _route_first_turn(
        self, user_input: str, urgency_override: Optional[str] = None
    ) -> Tuple[str, float, float]:
        """Classificazione al primo turno usando urgency_override o SmartRouter."""
        # Usa override pre-calcolato da LLMJudge nel slot filler (evita doppia chiamata LLM)
        if urgency_override == "emergency":
            return ("A", 3.0, 1.0)
        elif urgency_override == "mental_health":
            return ("B", 3.0, 1.0)
        elif urgency_override == "info":
            return ("INFO", 0.0, 1.0)

        # Controlla pattern INFO prima del router generico
        if self._is_info_query(user_input):
            return ("INFO", 0.0, 0.95)

        # SmartRouter come safety net
        from .smart_router import SmartRouter
        path, meta = SmartRouter.route(user_input)
        urgency = float(meta.get("urgency", 3))
        return (path, urgency, 0.7)

    def _is_info_query(self, user_input: str) -> bool:
        """Verifica se l'input è una query informativa tramite pattern regex."""
        text_lower = user_input.lower()
        for pattern in self.INFO_PATTERNS:
            if re.search(pattern, text_lower):
                logger.debug(f"RouteArbitrator: INFO pattern matched: {pattern}")
                return True
        return False

    def _compute_urgency(self, user_input: str) -> float:
        """Calcola urgency score per continuità nel branch attuale."""
        text_lower = user_input.lower()
        count = sum(1 for kw in self.EMERGENCY_KEYWORDS if kw in text_lower)
        return min(5.0, 3.0 + count)
