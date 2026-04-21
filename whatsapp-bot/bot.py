import logging
import os
import sys
import copy
import time
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional, Tuple
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
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

class MissingEnvironmentVariable(Exception):
    def __init__(self, *args):
        super().__init__(*args)


app = FastAPI(title="WhatsApp Bot Webhook", version="1.0.0")

logging.basicConfig(level=logging.INFO)
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
conversation_locks: Dict[str, Lock] = {}


class InMemoryConversationState:
    """State adapter compatible with StateManager API, isolated per conversation."""

    def __init__(self):
        self._data: Dict[str, Any] = {}

    def init(self) -> None:
        for key, default_value in DEFAULT_STATE.items():
            if key not in self._data:
                if isinstance(default_value, (list, dict)):
                    self._data[key] = copy.deepcopy(default_value)
                else:
                    self._data[key] = default_value

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def update(self, updates: Dict[str, Any]) -> None:
        for key, value in updates.items():
            self._data[key] = value


class InMemoryTTLMessageDeduplicator:
    """Thread-safe webhook deduplication by message_id with TTL cleanup."""

    def __init__(self, ttl_seconds: int = 20 * 60, max_entries: int = 50_000):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._lock = Lock()
        self._seen_at: Dict[str, float] = {}

    def _cleanup_locked(self, now: float) -> None:
        expired_before = now - self.ttl_seconds
        expired_keys = [mid for mid, ts in self._seen_at.items() if ts < expired_before]
        for mid in expired_keys:
            self._seen_at.pop(mid, None)

        # Safety cap in case of abnormal traffic
        if len(self._seen_at) > self.max_entries:
            oldest = sorted(self._seen_at.items(), key=lambda item: item[1])
            to_remove = len(self._seen_at) - self.max_entries
            for mid, _ in oldest[:to_remove]:
                self._seen_at.pop(mid, None)

    def seen_or_mark(self, message_id: str) -> bool:
        """Return True if already seen in TTL window; otherwise mark and return False."""
        now = time.monotonic()
        with self._lock:
            self._cleanup_locked(now)
            if message_id in self._seen_at:
                return True
            self._seen_at[message_id] = now
            return False


deduplicator = InMemoryTTLMessageDeduplicator(ttl_seconds=20 * 60)


def get_or_create_controller(conversation_key: str) -> TriageControllerV3:
    """One controller instance per WhatsApp conversation/user."""
    with controllers_lock:
        if conversation_key not in controllers:
            controller = TriageControllerV3()
            # IMPORTANT: isolate state from Streamlit singleton to prevent cross-talk.
            isolated_state = InMemoryConversationState()
            isolated_state.init()
            controller.state = isolated_state
            if hasattr(controller, "fsm") and hasattr(controller.fsm, "state"):
                controller.fsm.state = isolated_state
            controllers[conversation_key] = controller
            logger.info("🆕 New controller created for conversation=%s", conversation_key)
        return controllers[conversation_key]


def get_or_create_conversation_lock(conversation_key: str) -> Lock:
    """One mutex per conversation to serialize load→process→save and reply."""
    with controllers_lock:
        lock = conversation_locks.get(conversation_key)
        if lock is None:
            lock = Lock()
            conversation_locks[conversation_key] = lock
        return lock


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

def extract_message_event(body: dict) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract (message_id, phone_number, text) from Meta payload for text messages only."""
    if not body.get("object"):
        return None, None, None

    try:
        change_value = body["entry"][0]["changes"][0]["value"]
        messages = change_value.get("messages") or []
        if not messages:
            return None, None, None

        message = messages[0]
        message_id = message.get("id")
        phone_number = message.get("from")
        message_type = message.get("type")
        if message_type != "text":
            return None, None, None
        text_body = message.get("text", {}).get("body", "").strip()
        if message_id and phone_number and text_body:
            return message_id, phone_number, text_body
    except (IndexError, KeyError, TypeError):
        return None, None, None

    return None, None, None


def extract_text_message(body: dict) -> Tuple[Optional[str], Optional[str]]:
    """Backward-compatible wrapper: returns only (phone_number, text)."""
    _, phone_number, text = extract_message_event(body)
    return phone_number, text


def _process_incoming_message(phone_number: str, text: str, message_id: str) -> None:
    """Background worker: process one WhatsApp message atomically for one conversation."""
    logger.info("start_processing message_id=%s phone_number=%s", message_id, phone_number)
    conversation_lock = get_or_create_conversation_lock(phone_number)

    with conversation_lock:
        try:
            controller = get_or_create_controller(phone_number)
            _load_conversation_state(phone_number, controller)
            reply = controller.process_user_input(text)
            _save_conversation_state(phone_number, controller)

            assistant_text = (reply or {}).get("assistant_response", "")
            if not assistant_text:
                assistant_text = "Grazie, ho ricevuto il tuo messaggio. Puoi darmi qualche dettaglio in più?"

            send_reply(phone_number, assistant_text)
            logger.info("end_processing message_id=%s phone_number=%s status=ok", message_id, phone_number)
        except Exception as exc:
            logger.exception(
                "processing_error message_id=%s phone_number=%s error=%s",
                message_id,
                phone_number,
                exc,
            )
            try:
                send_reply(
                    phone_number,
                    "Si è verificato un problema temporaneo. Riprova tra qualche secondo.",
                )
            except Exception as fallback_exc:
                logger.exception(
                    "fallback_send_failed message_id=%s phone_number=%s error=%s",
                    message_id,
                    phone_number,
                    fallback_exc,
                )


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
async def handle_messages(request: Request, background_tasks: BackgroundTasks) -> dict:
    try:
        body = await request.json()
    except Exception:
        logger.warning("invalid_json_payload")
        return {"status": "ok"}

    message_id, phone_number, text = extract_message_event(body)
    if not message_id or not phone_number or not text:
        logger.info("ignore_event reason=non_text_or_incomplete")
        return {"status": "ok"}

    is_duplicate = deduplicator.seen_or_mark(message_id)
    logger.info(
        "webhook_received message_id=%s phone_number=%s dedup=%s",
        message_id,
        phone_number,
        "hit" if is_duplicate else "miss",
    )

    if is_duplicate:
        return {"status": "ok"}

    logger.info("enqueue_processing message_id=%s phone_number=%s", message_id, phone_number)
    background_tasks.add_task(_process_incoming_message, phone_number, text, message_id)
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("bot:app", host="0.0.0.0", port=PORT, reload=False)