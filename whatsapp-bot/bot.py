import logging
import os
import sys
import copy
from pathlib import Path
from threading import Lock
from typing import Dict, Optional, Tuple
from dotenv import load_dotenv


# Allow running this file directly: python .\whatsapp-bot\bot.py
# by ensuring project root is on sys.path.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load workspace .env when running directly (python .\whatsapp-bot\bot.py)
load_dotenv(PROJECT_ROOT / ".env", override=True)

from siraya.controllers.triage_controller_v3 import TriageControllerV3
from siraya.core.state_manager import DEFAULT_STATE, StateKeys

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

class MissingEnvironmentVariable(Exception):
    def __init__(self, *args):
        super().__init__(*args)


app = FastAPI(title="WhatsApp Bot Webhook", version="1.0.0")

logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger("whatsapp-bot")

# Silence Streamlit "missing ScriptRunContext" warnings in bare mode
logging.getLogger("streamlit").setLevel(logging.ERROR)
logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").setLevel(logging.ERROR)


# --- CONFIGURAZIONE ---
# Per produzione usa variabili d'ambiente, non hardcoded secrets.
def _get_env_clean(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    return value or None


VERIFY_TOKEN = _get_env_clean("WHATSAPP_VERIFY_TOKEN")
WHATSAPP_TOKEN = _get_env_clean("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = _get_env_clean("WHATSAPP_PHONE_NUMBER_ID")
PORT = int(os.getenv("PORT", "5000"))

if not VERIFY_TOKEN or not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
    logger.error(f"ERRORE GRAVE, NON TROVATI TOKEN O ID NUMERO:\nVERIFY_TOKEN:{VERIFY_TOKEN}\nWHATSAPP_TOKEN:{WHATSAPP_TOKEN}\nPHONE_NUMBER_ID:{PHONE_NUMBER_ID}")
    raise MissingEnvironmentVariable()

controllers: Dict[str, TriageControllerV3] = {}
controllers_lock = Lock()
conversation_states: Dict[str, dict] = {}


def get_or_create_controller(conversation_key: str) -> TriageControllerV3:
    """One controller instance per WhatsApp conversation/user."""
    with controllers_lock:
        if conversation_key not in controllers:
            controllers[conversation_key] = TriageControllerV3()
            logger.info("🆕 New controller created for conversation=%s", conversation_key)
        return controllers[conversation_key]


def _load_conversation_state(conversation_key: str, controller: TriageControllerV3) -> None:
    """Restore saved state snapshot into controller state before processing."""
    state = controller.state
    try:
        state.init()
    except Exception:
        pass

    with controllers_lock:
        snapshot = conversation_states.get(conversation_key)

    if snapshot is None:
        return

    for key, value in snapshot.items():
        state.set(key, copy.deepcopy(value))


def _save_conversation_state(conversation_key: str, controller: TriageControllerV3) -> None:
    """Persist current controller state snapshot for this conversation."""
    state = controller.state

    snapshot = {}
    for key in DEFAULT_STATE.keys():
        snapshot[key] = copy.deepcopy(state.get(key, DEFAULT_STATE[key]))

    # Keep a stable session id per WhatsApp number
    existing_session_id = snapshot.get(StateKeys.SESSION_ID)
    if not existing_session_id:
        snapshot[StateKeys.SESSION_ID] = f"wa-{conversation_key}"

    with controllers_lock:
        conversation_states[conversation_key] = snapshot

def extract_text_message(body: dict) -> Tuple[Optional[str], Optional[str]]:
    """Estrae (phone_number, text) dal payload Meta se presente."""
    if not body.get("object"):
        return None, None

    try:
        logger.debug(body)
        message = body["entry"][0]["changes"][0]["value"]["messages"][0]
        phone_number = message.get("from")
        text_body = message.get("text", {}).get("body", "").strip()
        if phone_number and text_body:
            return phone_number, text_body
    except (IndexError, KeyError, TypeError):
        return None, None

    return None, None


def send_reply(to_number: str, text: str) -> None:
    """Invia risposta usando WhatsApp Cloud API."""
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        logger.error("Configurazione mancante: WHATSAPP_TOKEN o WHATSAPP_PHONE_NUMBER_ID")
        raise HTTPException(status_code=500, detail="Missing WhatsApp API configuration")

    url = f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "text": {"body": text},
    }

    response = requests.post(url, headers=headers, json=data, timeout=15)
    if response.status_code >= 400:
        error_code = None
        error_subcode = None
        try:
            payload = response.json()
            error_obj = payload.get("error", {})
            error_code = error_obj.get("code")
            error_subcode = error_obj.get("error_subcode")
        except Exception:
            payload = None

        logger.error("Errore invio messaggio WhatsApp: %s", response.text)

        if error_code == 190:
            logger.error(
                "Token WhatsApp non valido/scaduto (code=190, subcode=%s). "
                "Verifica che il token sia quello Cloud API corrente, associato allo stesso Business/Phone Number ID.",
                error_subcode,
            )
        raise HTTPException(status_code=502, detail="Failed to send WhatsApp message")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# 1) Verifica webhook da parte di Meta (GET)
@app.get("/webhook", response_class=PlainTextResponse)
def verify_webhook(
    hub_mode: Optional[str] = Query(default=None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(default=None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(default=None, alias="hub.challenge"),
):
    if not VERIFY_TOKEN:
        raise HTTPException(status_code=500, detail="Missing WHATSAPP_VERIFY_TOKEN")

    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return hub_challenge or ""

    raise HTTPException(status_code=403, detail="Forbidden")

# 2) Ricezione messaggi WhatsApp (POST)
@app.post("/webhook")
async def handle_messages(request: Request) -> dict:
    body = await request.json()
    phone_number, text = extract_text_message(body)

    if phone_number and text:
        try:
            controller = get_or_create_controller(phone_number)
            _load_conversation_state(phone_number, controller)
            reply = controller.process_user_input(text)
            _save_conversation_state(phone_number, controller)
            assistant_text = (reply or {}).get("assistant_response", "")
            if not assistant_text:
                assistant_text = "Grazie, ho ricevuto il tuo messaggio. Puoi darmi qualche dettaglio in più?"
            send_reply(phone_number, assistant_text)
        except Exception as exc:
            logger.exception("❌ Error while processing message for %s: %s", phone_number, exc)
            try:
                send_reply(
                    phone_number,
                    "Si è verificato un problema temporaneo. Riprova tra qualche secondo.",
                )
            except Exception as fallback_exc:
                logger.exception(
                    "❌ Fallback send failed for %s: %s",
                    phone_number,
                    fallback_exc,
                )

    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("bot:app", host="0.0.0.0", port=PORT, reload=False)