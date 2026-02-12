"""
SIRAYA LLM Phase Handlers
V4.0: Modular phase architecture.

Each phase encapsulates one conversation macro-phase:
- IntakePhase:          Accoglienza & slot filling (ZERO RAG)
- TriagePhase:          Triage clinico A/B/C (Lazy RAG)
- RecommendationPhase:  SBAR + facility search
- InfoPhase:            Branch informativo
"""

from .intake_phase import IntakePhase
from .triage_phase import TriagePhase
from .recommendation_phase import RecommendationPhase
from .info_phase import InfoPhase

__all__ = [
    "IntakePhase",
    "TriagePhase",
    "RecommendationPhase",
    "InfoPhase",
]

