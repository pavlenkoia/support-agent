from pathlib import Path

from app.integrations.vk.longpoll import VKLongPollClient, VKLongPollState
from app.workers.vk_main import FileLongPollStateStore, process_once


class StubVKAPIClient:
    def __init__(self, poll_responses=None):
        self.poll_responses = list(poll_responses or [])
        self.refresh_calls = 0

    def get_longpoll_server(self, group_id):
        self.refresh_calls += 1
        return {"ok": True, "response": {"server": "https://lp.vk.test", "key": f"k{self.refresh_calls}", "ts": f"{100 + self.refresh_calls}"}}

    def check_longpoll(self, **kwargs):
        return self.poll_responses.pop(0)


class RecordingGateway:
    def __init__(self):
        self.events = []

    def handle_event(self, event):
        self.events.append(event)
        return {"ok": True}


class RecordingAcker:
    def __init__(self):
        self.states = []

    def ack(self, state):
        self.states.append(state)


def test_vk_longpoll_handles_failed_1_by_advancing_ts() -> None:
    client = StubVKAPIClient(poll_responses=[{"ok": True, "failed": 1, "ts": "555"}])
    poller = VKLongPollClient(api_client=client, group_id="55")
    state, updates = poller.poll_once(VKLongPollState(server="https://lp.vk.test", key="key", ts="10"))

    assert state.ts == "555"
    assert updates == []
    assert client.refresh_calls == 0


def test_vk_longpoll_handles_failed_2_by_refreshing_key() -> None:
    client = StubVKAPIClient(poll_responses=[{"ok": True, "failed": 2}])
    poller = VKLongPollClient(api_client=client, group_id="55")
    state, updates = poller.poll_once(VKLongPollState(server="https://lp.vk.test", key="key", ts="10"))

    assert state.key == "k1"
    assert state.ts == "101"
    assert updates == []
    assert client.refresh_calls == 1


def test_vk_longpoll_handles_failed_3_by_refreshing_full_state() -> None:
    client = StubVKAPIClient(poll_responses=[{"ok": True, "failed": 3}])
    poller = VKLongPollClient(api_client=client, group_id="55")
    state, updates = poller.poll_once(VKLongPollState(server="https://lp.vk.test", key="key", ts="10"))

    assert state.server == "https://lp.vk.test"
    assert state.key == "k1"
    assert state.ts == "101"
    assert updates == []
    assert client.refresh_calls == 1


def test_vk_process_once_routes_updates_and_acks_state() -> None:
    client = StubVKAPIClient(poll_responses=[{"ok": True, "ts": "777", "updates": [{"type": "message_new"}]}])
    poller = VKLongPollClient(api_client=client, group_id="55")
    gateway = RecordingGateway()
    acker = RecordingAcker()

    result = process_once(gateway=gateway, poller=poller, acker=acker, state=VKLongPollState(server="https://lp.vk.test", key="key", ts="10"))

    assert result["processed"] == 1
    assert gateway.events == [{"type": "message_new"}]
    assert acker.states[-1].ts == "777"


def test_vk_longpoll_treats_transport_timeout_as_empty_poll() -> None:
    client = StubVKAPIClient(poll_responses=[{"ok": False, "reason": "transport_error:The read operation timed out"}])
    poller = VKLongPollClient(api_client=client, group_id="55")

    original = VKLongPollState(server="https://lp.vk.test", key="key", ts="10")
    state, updates = poller.poll_once(original)

    assert state is original
    assert state.ts == "10"
    assert updates == []
    assert client.refresh_calls == 0


def test_vk_state_store_roundtrip(tmp_path: Path) -> None:
    store = FileLongPollStateStore(str(tmp_path / "vk-state.json"))
    state = VKLongPollState(server="https://lp.vk.test", key="abc", ts="123")

    store.save(state)
    restored = store.load()

    assert restored == state
