"""
SIRAYA Health Navigator — Symptom Normalizer
Extracted from llm_utils.py during V5 refactoring.

Provides fuzzy-matching medicalization of free-text symptoms.
Example: "mi gira la testa" → "Vertigini"
"""

import logging
import difflib

from ..config.settings import ClinicalMappings

logger = logging.getLogger(__name__)


class SymptomNormalizer:
    """Normalizza i sintomi descritti dall'utente usando fuzzy matching."""

    def __init__(self):
        self.canonical_kb = ClinicalMappings.CANONICAL_KB
        self.stop_words = ClinicalMappings.STOP_WORDS
        logger.info("SymptomNormalizer initialized")

    def _preprocess(self, text: str) -> str:
        text = text.lower().strip()
        words = text.split()
        filtered_words = [w for w in words if w not in self.stop_words]
        return " ".join(filtered_words)

    def normalize(self, user_symptom: str, threshold: float = 0.6) -> str:
        """
        Normalizza il sintomo usando fuzzy matching.

        Returns:
            Sintomo medicalizzato (es. "mi gira la testa" → "Vertigini")
        """
        preprocessed = self._preprocess(user_symptom)
        best_match = None
        best_score = 0.0

        for canonical_term, medical_term in self.canonical_kb.items():
            score = difflib.SequenceMatcher(
                None, preprocessed, canonical_term.lower()
            ).ratio()
            if score > best_score:
                best_score = score
                best_match = canonical_term

        if best_score >= threshold and best_match:
            medical = self.canonical_kb.get(best_match, best_match)
            logger.info(
                f"Normalized '{user_symptom}' → '{medical}' (score: {best_score:.2f})"
            )
            return medical

        return user_symptom

