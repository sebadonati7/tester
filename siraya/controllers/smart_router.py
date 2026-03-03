"""
Smart Router - Determina il percorso di triage
V3.1: Aggiunto scoring emergenze e check_escalation() per escalation C→A
"""

from typing import Tuple, Dict, List
import re


class SmartRouter:
    """
    Router che analizza l'input utente e determina il percorso.

    Percorsi:
    - A: EMERGENZA (Red/Orange)
    - B: SALUTE MENTALE (Black)
    - C: STANDARD (Green/Yellow)
    - INFO: Richiesta informazioni
    """

    # Keyword per emergenze (Percorso A)
    EMERGENCY_KEYWORDS: List[str] = [
        "dolore petto", "dolore toracico", "infarto", "cuore",
        "non respiro", "soffoco", "ictus", "paralisi",
        "emorragia", "sangue abbondante", "svenuto",
        "convulsioni", "trauma cranico", "incosciente"
    ]

    # Keyword per salute mentale (Percorso B)
    MENTAL_HEALTH_KEYWORDS: List[str] = [
        "depresso", "ansia", "panico", "suicidio",
        "farmi del male", "non ce la faccio", "voglio morire",
        "autolesionismo", "attacco di panico", "crisi"
    ]

    # Keyword per info (Branch INFO)
    INFO_KEYWORDS: List[str] = [
        "orari", "dove si trova", "come prenot", "numero",
        "indirizzo", "costo", "ticket", "prelievi"
    ]

    # Keyword di escalation: se appaiono DURANTE percorso C → possibile switch a A
    ESCALATION_KEYWORDS: List[str] = [
        "peggiorato", "peggiora", "non passa", "sempre più forte",
        "non riesco a muovermi", "vedo doppio", "perdo sangue",
        "febbre altissima", "svengo", "confuso", "non respiro bene",
        "sta peggiorando", "mi sento svenire", "non mi reggo in piedi"
    ]

    @classmethod
    def route(cls, user_message: str) -> Tuple[str, Dict]:
        """
        Determina il percorso basandosi sul messaggio con scoring.

        Args:
            user_message: Input utente

        Returns:
            (percorso, metadata)
        """
        msg_lower = user_message.lower()

        # 1. Check EMERGENZA con scoring (più keyword = urgenza più alta)
        emergency_score = 0
        emergency_trigger = None
        for keyword in cls.EMERGENCY_KEYWORDS:
            if keyword in msg_lower:
                emergency_score += 1
                if emergency_trigger is None:
                    emergency_trigger = keyword

        if emergency_score > 0:
            return ("A", {
                "reason": "emergency_detected",
                "trigger": emergency_trigger,
                "urgency": min(5, 3 + emergency_score),
                "score": emergency_score,
                "message": "Rilevata possibile emergenza medica"
            })

        # 2. Check SALUTE MENTALE
        for keyword in cls.MENTAL_HEALTH_KEYWORDS:
            if keyword in msg_lower:
                return ("B", {
                    "reason": "mental_health_crisis",
                    "trigger": keyword,
                    "message": "Rilevato disagio psicologico"
                })

        # 3. Check INFORMAZIONI
        for keyword in cls.INFO_KEYWORDS:
            if keyword in msg_lower:
                return ("INFO", {
                    "reason": "service_inquiry",
                    "trigger": keyword,
                    "message": "Richiesta informazioni servizi"
                })

        # 4. Default: TRIAGE STANDARD
        return ("C", {
            "reason": "standard_triage",
            "message": "Triage standard"
        })

    @classmethod
    def check_escalation(cls, user_message: str) -> bool:
        """
        Controlla se il messaggio indica un peggioramento
        durante un percorso C attivo → dovrebbe diventare A.

        Ritorna True se il punteggio complessivo (keyword escalation +
        keyword emergenza) è ≥ 2, oppure se c'è una keyword emergenza diretta.

        Returns:
            True se escalation necessaria
        """
        msg_lower = user_message.lower()
        score = 0

        for keyword in cls.ESCALATION_KEYWORDS:
            if keyword in msg_lower:
                score += 1

        for keyword in cls.EMERGENCY_KEYWORDS:
            if keyword in msg_lower:
                score += 2  # le keyword emergenza pesano di più

        return score >= 2

    @classmethod
    def extract_location(cls, user_message: str) -> str:
        """
        Estrae località dal messaggio.

        Returns:
            Nome comune o "Non specificato"
        """
        patterns = [
            r'(?:mi trovo|sono|abito|vivo)\s+a\s+([A-Z][a-zà-ù]+)',
            r'(?:città|comune)\s+di\s+([A-Z][a-zà-ù]+)',
            r'\ba\s+([A-Z][a-zà-ù]{3,})\b'
        ]

        for pattern in patterns:
            match = re.search(pattern, user_message)
            if match:
                return match.group(1)

        return "Non specificato"
