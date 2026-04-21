import importlib.util
import os
import sys
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).parent.parent
BOT_PATH = PROJECT_ROOT / "whatsapp-bot" / "bot.py"


def _load_bot_module(module_name: str):
    os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "test-verify")
    os.environ.setdefault("WHATSAPP_TOKEN", "test-token")
    os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "123456")

    spec = importlib.util.spec_from_file_location(module_name, str(BOT_PATH))
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _build_text_payload(message_id: str, phone_number: str, text: str) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": message_id,
                                    "from": phone_number,
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ]
                        }
                    }
                ]
            }
        ],
    }


def test_duplicate_delivery_same_message_id_processed_once(monkeypatch):
    bot = _load_bot_module("wa_bot_test_dup")

    processed = []

    def fake_process(phone_number: str, text: str, message_id: str):
        processed.append((message_id, phone_number, text))

    monkeypatch.setattr(bot, "_process_incoming_message", fake_process)

    client = TestClient(bot.app)
    payload = _build_text_payload("wamid.dup-1", "393330001111", "Ciao")

    for _ in range(10):
        response = client.post("/webhook", json=payload)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    assert len(processed) == 1
    assert processed[0][0] == "wamid.dup-1"


def test_two_users_have_isolated_state():
    bot = _load_bot_module("wa_bot_test_isolation")

    c1 = bot.get_or_create_controller("393330001111")
    c2 = bot.get_or_create_controller("393330002222")

    assert c1 is not c2

    c1.state.set(bot.StateKeys.CURRENT_PHASE, "phase-user-1")
    assert c2.state.get(bot.StateKeys.CURRENT_PHASE) != "phase-user-1"


def test_same_user_parallel_messages_are_serialized(monkeypatch):
    bot = _load_bot_module("wa_bot_test_serial")

    timeline = []
    sent = []

    class DummyController:
        def __init__(self):
            self.state = bot.InMemoryConversationState()
            self.state.init()

        def process_user_input(self, text: str):
            timeline.append(("start", text, time.monotonic()))
            current = self.state.get("counter", 0)
            time.sleep(0.05)
            self.state.set("counter", current + 1)
            timeline.append(("end", text, time.monotonic()))
            return {"assistant_response": f"ok-{current + 1}"}

    dummy_controller = DummyController()

    monkeypatch.setattr(bot, "get_or_create_controller", lambda _: dummy_controller)
    monkeypatch.setattr(bot, "send_reply", lambda to_number, text: sent.append((to_number, text)))

    t1 = threading.Thread(target=bot._process_incoming_message, args=("393330003333", "msg-1", "wamid-1"))
    t2 = threading.Thread(target=bot._process_incoming_message, args=("393330003333", "msg-2", "wamid-2"))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(sent) == 2
    assert len(timeline) == 4

    # Must be strictly serialized: start/end/start/end (or the reverse by scheduling).
    assert timeline[0][0] == "start"
    assert timeline[1][0] == "end"
    assert timeline[2][0] == "start"
    assert timeline[3][0] == "end"

    # Counter must progress without race lost updates.
    texts = sorted([msg_text for _, msg_text in sent])
    assert texts == ["ok-1", "ok-2"]
