from app.integrations.vk.sender import VKSender


class StubClient:
    def __init__(self):
        self.calls = []

    def send_message(self, *, peer_id, text, random_id):
        self.calls.append({"peer_id": peer_id, "text": text, "random_id": random_id})
        return {"ok": True, "response": 12345}


def test_vk_sender_generates_integer_random_id_by_default() -> None:
    client = StubClient()
    sender = VKSender(client=client)

    result = sender.send_message(peer_id="185761966", text="Привет")

    assert result["ok"] is True
    assert client.calls
    random_id = client.calls[0]["random_id"]
    assert isinstance(random_id, str)
    assert random_id.isdigit()
    assert int(random_id) > 0
