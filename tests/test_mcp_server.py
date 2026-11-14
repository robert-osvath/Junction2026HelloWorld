import httpx

from mcp_server.client import AimoAPI, AimoAPIError


def run_immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("Test double unexpectedly required an event loop")


def test_client_forwards_request_and_response():
    class Client:
        async def request(self, method, path, *, params=None, json=None):
            self.seen = {"method": method, "path": path, "params": params, "body": json}
            return httpx.Response(200, json=[{"product_id": "463038"}], request=httpx.Request(method, "http://test" + path))

    client = Client()
    assert run_immediate(AimoAPI(client=client).request("GET", "/catalog/search", params={"q": "Eila"})) == [{"product_id": "463038"}]
    assert client.seen == {"method": "GET", "path": "/catalog/search", "params": {"q": "Eila"}, "body": None}


def test_client_exposes_http_failure_without_hiding_status():
    class Client:
        async def request(self, method, path, *, params=None, json=None):
            return httpx.Response(422, text="bad body", request=httpx.Request(method, "http://test" + path))

    try:
        run_immediate(AimoAPI(client=Client()).request("POST", "/offers", body={}))
    except AimoAPIError as exc:
        assert "422" in str(exc) and "bad body" in str(exc)
    else:
        raise AssertionError("Expected an AimoAPIError")
