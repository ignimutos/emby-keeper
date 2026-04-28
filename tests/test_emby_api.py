import asyncio

from embykeeper.emby.api import Emby
from embykeeper.schema import EmbyAccount


class FakeResponse:
    status_code = 200
    ok = True
    text = ""


class FakeSession:
    def __init__(self):
        self.requested_url = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, method, url, **kwargs):
        self.requested_url = url
        return FakeResponse()


def test_request_preserves_base_path_in_account_url():
    account = EmbyAccount(url="https://example.com/emby", username="user", password="pass")
    client = Emby(account)
    session = FakeSession()
    client._get_session = lambda: session

    asyncio.run(client._request("GET", "/Users/AuthenticateByName", _login=True))

    assert session.requested_url.endswith("/emby/Users/AuthenticateByName")
