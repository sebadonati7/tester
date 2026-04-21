import copy
import logging
import time
from threading import Lock
from typing import Any, Callable, Dict

from siraya.controllers.triage_controller_v3 import TriageControllerV3
from siraya.core.state_manager import DEFAULT_STATE, StateKeys

logger = logging.getLogger("whatsapp-bot")


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


class ConversationRuntime:
    """State and controller runtime for WhatsApp conversations."""

    def __init__(self, dedup_ttl_seconds: int = 20 * 60):
        self._controllers: Dict[str, TriageControllerV3] = {}
        self._conversation_states: Dict[str, dict] = {}
        self._conversation_locks: Dict[str, Lock] = {}
        self._runtime_lock = Lock()
        self._deduplicator = InMemoryTTLMessageDeduplicator(ttl_seconds=dedup_ttl_seconds)

    def seen_or_mark_message(self, message_id: str) -> bool:
        return self._deduplicator.seen_or_mark(message_id)

    def _get_or_create_controller(self, conversation_key: str) -> TriageControllerV3:
        with self._runtime_lock:
            if conversation_key not in self._controllers:
                controller = TriageControllerV3()
                # IMPORTANT: isolate state from Streamlit singleton to prevent cross-talk.
                isolated_state = InMemoryConversationState()
                isolated_state.init()
                controller.state = isolated_state
                if hasattr(controller, "fsm") and hasattr(controller.fsm, "state"):
                    controller.fsm.state = isolated_state
                self._controllers[conversation_key] = controller
                logger.info("new_controller_created conversation=%s", conversation_key)
            return self._controllers[conversation_key]

    def _get_or_create_conversation_lock(self, conversation_key: str) -> Lock:
        with self._runtime_lock:
            lock = self._conversation_locks.get(conversation_key)
            if lock is None:
                lock = Lock()
                self._conversation_locks[conversation_key] = lock
            return lock

    def _load_conversation_state(self, conversation_key: str, controller: TriageControllerV3) -> None:
        state = controller.state
        try:
            state.init()
        except Exception:
            pass

        with self._runtime_lock:
            snapshot = self._conversation_states.get(conversation_key)

        if snapshot is None:
            return

        for key, value in snapshot.items():
            state.set(key, copy.deepcopy(value))

    def _save_conversation_state(self, conversation_key: str, controller: TriageControllerV3) -> None:
        state = controller.state

        snapshot = {}
        for key in DEFAULT_STATE.keys():
            snapshot[key] = copy.deepcopy(state.get(key, DEFAULT_STATE[key]))

        # Keep a stable session id per WhatsApp number
        existing_session_id = snapshot.get(StateKeys.SESSION_ID)
        if not existing_session_id:
            snapshot[StateKeys.SESSION_ID] = f"wa-{conversation_key}"

        with self._runtime_lock:
            self._conversation_states[conversation_key] = snapshot

    def process_message(
        self,
        phone_number: str,
        text: str,
        message_id: str,
        send_reply: Callable[[str, str], None],
    ) -> None:
        """Process one message atomically for one conversation and send the reply."""
        logger.info("start_processing message_id=%s phone_number=%s", message_id, phone_number)
        conversation_lock = self._get_or_create_conversation_lock(phone_number)

        with conversation_lock:
            try:
                controller = self._get_or_create_controller(phone_number)
                self._load_conversation_state(phone_number, controller)
                reply = controller.process_user_input(text)
                self._save_conversation_state(phone_number, controller)

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
