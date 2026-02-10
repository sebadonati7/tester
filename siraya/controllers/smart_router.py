"""
Smart Router - Determina il percorso di triage
"""

from typing import Tuple, Dict
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
    EMERGENCY_KEYWORDS = [
        "dolore petto", "dolore toracico", "infarto", "cuore",
        "non respiro", "soffoco", "ictus", "paralisi",
        "emorragia", "sangue abbondante", "svenuto",
        "convulsioni", "trauma cranico", "incosciente"
    ]
    
    # Keyword per salute mentale (Percorso B)
    MENTAL_HEALTH_KEYWORDS = [
        "depresso", "ansia", "panico", "suicidio",
        "farmi del male", "non ce la faccio", "voglio morire",
        "autolesionismo", "attacco di panico", "crisi"
    ]
    
    # Keyword per info (Branch INFO)
    INFO_KEYWORDS = [
        "orari", "dove si trova", "come prenot", "numero",
        "indirizzo", "costo", "ticket", "prelievi"
    ]
    
    @classmethod
    def route(cls, user_message: str) -> Tuple[str, Dict]:
        """
        Determina il percorso basandosi sul messaggio.
        
        Args:
            user_message: Input utente
            
        Returns:
            (percorso, metadata)
            
        Esempi:
            ("A", {"reason": "dolore_toracico", "urgency": 5})
            ("B", {"reason": "mental_health_crisis"})
            ("C", {"reason": "standard_triage"})
            ("INFO", {"reason": "service_inquiry"})
        """
        msg_lower = user_message.lower()
        
        # 1. Check EMERGENZA (priorità massima)
        for keyword in cls.EMERGENCY_KEYWORDS:
            if keyword in msg_lower:
                return ("A", {
                    "reason": "emergency_detected",
                    "trigger": keyword,
                    "urgency": 5,
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
    def extract_location(cls, user_message: str) -> str:
        """
        Estrae località dal messaggio.
        
        Returns:
            Nome comune o "Non specificato"
        """
        # Pattern per "mi trovo a X", "sono a X", "abito a X"
        patterns = [
            r'(?:mi trovo|sono|abito|vivo)\s+a\s+([A-Z][a-zà-ù]+)',
            r'(?:città|comune)\s+di\s+([A-Z][a-zà-ù]+)',
            r'\ba\s+([A-Z][a-zà-ù]{3,})\b'  # Almeno 3 lettere maiuscola iniziale
        ]
        
        for pattern in patterns:
            match = re.search(pattern, user_message)
            if match:
                return match.group(1)
        
        return "Non specificato"
