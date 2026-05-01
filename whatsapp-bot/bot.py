import logging
import os
import sys
import threading
from pathlib import Path
from typing import Optional, Tuple
from dotenv import load_dotenv
from typing import Any, List, Optional, Tuple
import json
import re
from urllib.parse import quote_plus


# Allow running this file directly: python .\whatsapp-bot\bot.py
# by ensuring project root is on sys.path.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load workspace .env when running directly (python .\whatsapp-bot\bot.py)
load_dotenv(PROJECT_ROOT / ".env", override=True)

from siraya.controllers.triage_controller_v3 import TriageControllerV3
from siraya.webhooks.conversation_runtime import ConversationRuntime

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

runtime = ConversationRuntime(dedup_ttl_seconds=20 * 60)
PAYLOAD_LOG_PATH = PROJECT_ROOT / "Payload_risposte.json"
PAYLOAD_LOG_LOCK = threading.Lock()

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

def _markdown_to_whatsapp(text: str) -> str:
    """
    Adatta Markdown generico a uno stile più compatibile con WhatsApp.
    Nota: è una conversione 'best effort'.
    """
    t = text.strip()

    # Bold markdown **x** -> WhatsApp *x*
    t = re.sub(r"\*\*(.+?)\*\*", r"*\1*", t)

    # Heading markdown (#, ##) -> linea normale
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.MULTILINE)

    # Liste markdown "- item" o "* item" -> "• item"
    t = re.sub(r"^\s*[-*]\s+", "• ", t, flags=re.MULTILINE)

    # Link markdown [txt](url) -> "txt (url)"
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", t)

    return t


def _append_google_maps_link(text: str) -> str:
    """
    Se rileva un indirizzo nella risposta, aggiunge un link Google Maps cliccabile
    per avviare rapidamente la navigazione.
    """
    if not text:
        return text

    if re.search(r"https?://(?:www\.)?google\.[^\s]*/maps", text, flags=re.IGNORECASE):
        return text

    lines = [line.strip() for line in text.splitlines()]
    address_candidate = None

    for idx, line in enumerate(lines):
        if line.startswith("📍"):
            for next_idx in range(idx + 1, min(idx + 4, len(lines))):
                candidate = lines[next_idx]
                if not candidate:
                    continue
                if candidate.startswith("📞"):
                    continue
                if re.match(r"https?://", candidate, flags=re.IGNORECASE):
                    continue
                # euristica: indirizzo contiene numero civico e virgola
                if re.search(r"\d", candidate) and "," in candidate:
                    address_candidate = candidate
                    break
        if address_candidate:
            break

    if not address_candidate:
        return text

    maps_url = f"https://www.google.com/maps/search/?api=1&query={quote_plus(address_candidate)}"
    return f"{text.rstrip()}\n\n🗺️ Avvia navigazione: {maps_url}"


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
        "text": {"body": _append_google_maps_link(_markdown_to_whatsapp(text))},
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

import datetime


@app.post("/webhook")
async def handle_messages(request: Request, background_tasks: BackgroundTasks) -> dict:
    try:
        body = await request.json()
    except Exception:
        logger.warning("invalid_json_payload")
        return {"status": "ok"}
    """ 
    # Best-effort payload logging (non deve bloccare il webhook)
    try:
        now = datetime.datetime.now().isoformat()
        log_entry = {
            "datetime": now,
            "payload": body,
        }

        with PAYLOAD_LOG_LOCK:
            payload_items = []

            if PAYLOAD_LOG_PATH.exists():
                try:
                    raw_content = PAYLOAD_LOG_PATH.read_text(encoding="utf-8").strip()
                    if raw_content:
                        parsed = json.loads(raw_content)
                        if isinstance(parsed, list):
                            payload_items = parsed
                except Exception:
                    payload_items = []

            payload_items.append(log_entry)
            PAYLOAD_LOG_PATH.write_text(
                json.dumps(payload_items, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    except Exception as exc:
        logger.warning("payload_file_log_failed error=%s", exc)
    """
    message_id, phone_number, text = extract_message_event(body)
    if not message_id or not phone_number or not text:
        logger.info("ignore_event reason=non_text_or_incomplete")
        return {"status": "ok"}

    is_duplicate = runtime.seen_or_mark_message(message_id)
    logger.info(
        "webhook_received message_id=%s phone_number=%s dedup=%s",
        message_id,
        phone_number,
        "hit" if is_duplicate else "miss",
    )

    if is_duplicate:
        return {"status": "ok"}

    logger.info("enqueue_processing message_id=%s phone_number=%s", message_id, phone_number)
    background_tasks.add_task(runtime.process_message, phone_number, text, message_id, send_reply)
    return {"status": "ok"}

@app.get("/ping", response_class=PlainTextResponse)
async def keepalive():
    return "UP"

if __name__ == "__main__":
    uvicorn.run("bot:app", host="0.0.0.0", port=PORT, reload=False)