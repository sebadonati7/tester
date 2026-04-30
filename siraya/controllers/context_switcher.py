"""
SIRAYA Context Switcher - Gestisce cambio di contesto durante il triage.
V1.0: Permettere richieste INFO/META durante fase clinica, poi riprrendere.

Problema: Una volta assegnato TRIAGE_BRANCH, il sistema è "locked" al quel ramo.
Soluzione: Intercettare richieste di cambio contesto PRIMA della FSM.
"""

import logging
from typing import Dict, Any, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class ContextType(Enum):
    """Tipi di richiesta di contesto."""
    TRIAGE = "triage"  # Continua triage in corso
    INFO = "info"      # Richiesta informazioni (orari, contatti, dove)
    META = "meta"      # Richiesta su come funziona il bot
    FEEDBACK = "feedback"  # Feedback/reclami
    RESTART = "restart"    # Ricomincia da capo


class ContextSwitcher:
    """
    Intercetta e gestisce cambi di contesto durante il triage.
    
    Flusso:
    1. Utente manda messaggio
    2. ContextSwitcher determina se è una richiesta INFO/META/FEEDBACK
    3. Se SÌ → salva stato triage, rispondi alla richiesta, chiedi se continuare
    4. Se NO → passa a normal triage flow
    """
    
    # Keywords per richieste INFO
    INFO_KEYWORDS = {
        "orari": "hours",
        "ore": "hours",
        "apertura": "hours",
        "chiusura": "hours",
        "dove": "location",
        "indirizzo": "location",
        "città": "location",
        "comune": "location",
        "contatti": "contact",
        "telefono": "contact",
        "email": "contact",
        "whatsapp": "contact",
        "prenot": "booking",
        "appuntamento": "booking",
        "disponibilit": "booking",
        "come funziona": "how",
        "come posso": "how",
        "chi sei": "how",
        "cosa fai": "how",
        "come funzioni": "how",
    }
    
    # Keywords per META (domande sul bot stesso)
    META_KEYWORDS = {
        "privacy": "privacy",
        "dati": "privacy",
        "gdpr": "privacy",
        "sicurezza": "privacy",
        "chi gestisce": "admin",
        "chi sei": "admin",
        "sviluppatore": "admin",
        "responsabile": "admin",
        "costo": "cost",
        "gratuito": "cost",
        "quanto costa": "cost",
        "prezzo": "cost",
        "aiuto": "help",
        "supporto": "help",
        "non funziona": "help",
        "errore": "help",
        "problema": "help",
    }
    
    # Keywords per RESTART
    RESTART_KEYWORDS = {
        "ricomincia": "restart",
        "riparti": "restart",
        "reset": "restart",
        "cancella": "restart",
        "nuovo": "restart",
        "ancora": "restart",
        "un'altra": "restart",
        "un altro": "restart",
        "altra persona": "restart",
    }
    
    # Keywords per FEEDBACK
    FEEDBACK_KEYWORDS = {
        "feedback": "feedback",
        "reclamo": "complaint",
        "non mi piace": "complaint",
        "mi dispiace": "complaint",
        "non funziona": "complaint",
        "non capisco": "complaint",
        "suggerimento": "suggestion",
        "migliora": "suggestion",
        "potrebbe": "suggestion",
    }
    
    @classmethod
    def detect_context(cls, user_input: str, current_triage_state: Optional[str] = None) -> Tuple[ContextType, Optional[str]]:
        """
        Detecta il tipo di contesto della richiesta.
        
        Args:
            user_input: Messaggio utente
            current_triage_state: Stato attuale del triage (per contestualizzare)
        
        Returns:
            (ContextType, category) dove category è il subtipo specifico (es. "info/hours")
        """
        text_lower = user_input.lower().strip()
        
        # PRIORITY 1: Se non siamo in triage, accetta tutto come triage
        if not current_triage_state or current_triage_state == "intake":
            return ContextType.TRIAGE, None
        
        # PRIORITY 2: Controlla RESTART (deve essere ovvio intento)
        for kw in cls.RESTART_KEYWORDS.keys():
            if kw in text_lower:
                # Ma se è in mezzo a una frase tipo "mi fa male ancora"
                if "ancora" in text_lower and "dolore" in text_lower:
                    return ContextType.TRIAGE, None
                logger.info(f"🔄 Context: RESTART detected ('{kw}')")
                return ContextType.RESTART, "restart"
        
        # PRIORITY 3: Controlla INFO
        for kw, category in cls.INFO_KEYWORDS.items():
            if kw in text_lower:
                logger.info(f"ℹ️ Context: INFO detected ('{kw}' → {category})")
                return ContextType.INFO, category
        
        # PRIORITY 4: Controlla META
        for kw, category in cls.META_KEYWORDS.items():
            if kw in text_lower:
                logger.info(f"❓ Context: META detected ('{kw}' → {category})")
                return ContextType.META, category
        
        # PRIORITY 5: Controlla FEEDBACK
        for kw, category in cls.FEEDBACK_KEYWORDS.items():
            if kw in text_lower:
                logger.info(f"💬 Context: FEEDBACK detected ('{kw}' → {category})")
                return ContextType.FEEDBACK, category
        
        # Default: è una risposta al triage
        return ContextType.TRIAGE, None
    
    @classmethod
    def handle_info_request(cls, category: str, collected_data: Dict[str, Any], data_loader=None) -> str:
        """Genera risposta a richiesta INFO."""
        location = collected_data.get("location", "Emilia-Romagna")
        
        if category == "hours":
            return (
                "📋 **Orari di apertura:**\n\n"
                "🏥 Pronto Soccorso: 24/7 (sempre aperto)\n"
                "🏢 Medico di Base: Lun-Ven 8:30-18:00, Sab 8:30-13:00\n"
                "🎯 CAU: Lun-Ven 8:00-20:00, Sab 8:00-14:00\n"
                "🧠 CSM (Salute Mentale): Lun-Ven 8:30-17:00\n\n"
                "⏱️ I tempi di attesa variano. Contatta la struttura per informazioni specifiche."
            )
        
        elif category == "location":
            return (
                f"📍 **Sei in provincia di {location}.**\n\n"
                "I servizi sanitari dell'Emilia-Romagna sono distribuiti su vari comuni.\n"
                "Durante il triage, potrai specificare la tua esatta posizione e ti guiderò "
                "alla struttura più vicina.\n\n"
                "Vuoi continuare con il triage?"
            )
        
        elif category == "contact":
            return (
                "☎️ **Contatti:**\n\n"
                "🚑 Emergenza: 118 (sempre disponibile)\n"
                "📞 CUP (Prenotazioni): 800.638.638\n"
                "🌐 Sito ER-Salute: www.salute.regione.emilia-romagna.it\n"
                "💬 WhatsApp: Questo bot è disponibile 24/7\n\n"
                "Posso aiutarti con il triage?"
            )
        
        elif category == "booking":
            return (
                "📅 **Prenotazione appuntamenti:**\n\n"
                "Le prenotazioni si gestiscono tramite:\n"
                "📞 CUP telefonico: 800.638.638\n"
                "🌐 Portale CUP online: www.cupweb.it\n"
                "🏥 Direttamente presso la struttura\n\n"
                "Per il triage di emergenza/urgenza, non è necessaria prenotazione.\n"
                "Continuo con la valutazione?"
            )
        
        return (
            "ℹ️ **Ulteriori informazioni:**\n\n"
            "Sono qui per aiutarti con il triage medico.\n"
            "Se hai altre domande, contatta il CUP al 800.638.638.\n\n"
            "Posso procedere con la valutazione dei tuoi sintomi?"
        )
    
    @classmethod
    def handle_meta_request(cls, category: str) -> str:
        """Genera risposta a richiesta META."""
        if category == "privacy":
            return (
                "🔒 **Privacy e Sicurezza:**\n\n"
                "✅ I tuoi dati sono protetti secondo GDPR\n"
                "✅ Non vengono condivisi con terze parti\n"
                "✅ Le conversazioni sono cifrate\n"
                "✅ Leggi l'informativa completa nel nostro sito\n\n"
                "Continuiamo con il triage?"
            )
        
        elif category == "admin":
            return (
                "ℹ️ **Chi sono:**\n\n"
                "Sono SIRAYA, un assistente di triage sanitario sviluppato dalla "
                "Regione Emilia-Romagna.\n\n"
                "Uso intelligenza artificiale per valutare i tuoi sintomi e "
                "guidarti verso la struttura sanitaria più appropriata.\n\n"
                "Continuiamo?"
            )
        
        elif category == "cost":
            return (
                "💰 **Costo:**\n\n"
                "SIRAYA è **completamente GRATUITO** 🎉\n\n"
                "È un servizio pubblico della Regione Emilia-Romagna "
                "per aiutarti a trovare il percorso sanitario giusto.\n\n"
                "Iniziamo il triage?"
            )
        
        elif category == "help":
            return (
                "🆘 **Hai un problema?**\n\n"
                "Se il bot non funziona correttamente:\n"
                "📞 Chiama il 118 per emergenze\n"
                "📞 Contatta il CUP: 800.638.638\n"
                "📧 Segnala il problema a: support@siraya.regione.er.it\n\n"
                "Riproviamo?"
            )
        
        return (
            "❓ **Domanda generica**\n\n"
            "Se non trovi risposta nel nostro sistema, contatta il CUP.\n\n"
            "Torniamo al triage?"
        )
    
    @classmethod
    def handle_feedback_request(cls, category: str, user_input: str) -> str:
        """Genera risposta a richiesta FEEDBACK."""
        if category == "complaint":
            return (
                "😔 **Mi dispiace che tu stia riscontrando problemi.**\n\n"
                "La tua segnalazione è importante. Puoi contattare:\n"
                "📧 support@siraya.regione.er.it\n"
                "📞 CUP: 800.638.638\n\n"
                "Posso comunque aiutarti con il triage adesso?"
            )
        
        elif category == "suggestion":
            return (
                "💡 **Grazie per il suggerimento!**\n\n"
                "I tuoi feedback ci aiutano a migliorare.\n"
                "Puoi inviare il tuo suggerimento a: feedback@siraya.regione.er.it\n\n"
                "Continuiamo con la valutazione?"
            )
        
        return (
            "💬 **Grazie per il tuo messaggio.**\n\n"
            "Continuo a supportarti nel triage medico.\n"
            "Vuoi procedere?"
        )
    
    @classmethod
    def should_pause_and_ask(cls, context_type: ContextType, current_phase: Optional[str]) -> bool:
        """
        Determina se dobbiamo pausa triage e chiedere se continuare.
        
        Regole:
        - Se in INTAKE → non pausa (ancora all'inizio)
        - Se in CHIEF_COMPLAINT, LOCALIZATION → pausa
        - Se in CLINICAL_TRIAGE+ → pausa SEMPRE (dati preziosi)
        """
        if not current_phase or current_phase == "intake":
            return False
        
        if context_type == ContextType.INFO:
            # Pausa INFO solo se in fasi avanzate
            return current_phase not in ("intake", "chief_complaint")
        
        if context_type == ContextType.META:
            # META sempre meriterebbe pausa se in fase avanzata
            return current_phase not in ("intake",)
        
        if context_type == ContextType.FEEDBACK:
            # FEEDBACK non interrompe triage
            return False
        
        return False