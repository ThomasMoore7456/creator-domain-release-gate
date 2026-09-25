import httpx

from ownership_service import InfraiClient, OwnershipRequest, prove_ownership


def test_release_waits_for_txt_and_resolves_user_after_verification():
    seen = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/v1/dns/domain/add":
            data = {"zone_id": "zone-content"}
        elif request.url.path == "/v1/dns/domain/verify":
            data = {"verified": True}
        elif request.url.path == "/v1/auth/user/get_by_email":
            assert request.url.params["email"] == "editor@example.org"
            data = {"id": "editor-1"}
        elif request.url.path in {"/v1/dns/record/delete", "/v1/dns/domain/delete"}:
            data = {"deleted": True}
        else:
            assert request.url.path == "/v1/dns/record/upsert"
            assert b'"zone_id":"zone-content"' in request.content
            data = {"record_id": "txt-1"}
        return httpx.Response(200, json={"ok": True, "data": data, "error": None, "metadata": {}})

    request = OwnershipRequest(
        domain="example.org", email="editor@example.org", build_id="build-7",
        release_id="release-7", txt_name="_ownership.example.org", txt_value="proof-7",
    )
    client = InfraiClient("test-key", transport=httpx.MockTransport(respond))
    try:
        result = prove_ownership(request, client)
    finally:
        client.http.close()

    assert result.release.state == "ready"
    assert result.user == {"id": "editor-1"}
    assert result.diagnostics["zone_id"] == "zone-content"
    assert seen == [
        ("POST", "/v1/dns/domain/add"),
        ("PUT", "/v1/dns/record/upsert"),
        ("POST", "/v1/dns/domain/verify"),
        ("GET", "/v1/auth/user/get_by_email"),
        ("DELETE", "/v1/dns/record/delete"),
        ("DELETE", "/v1/dns/domain/delete"),
    ]


def test_release_stays_pending_without_confirmation():
    class PendingClient:
        def call(self, method, path, payload):
            if path == "/v1/dns/domain/add":
                return {"zone_id": "zone-content"}
            if path == "/v1/dns/domain/verify":
                return {"verified": False}
            if path in {"/v1/dns/record/delete", "/v1/dns/domain/delete"}:
                return {"deleted": True}
            assert path == "/v1/dns/record/upsert"
            return {"record_id": "txt-1"}

    result = prove_ownership(
        OwnershipRequest(
            domain="example.org", email="editor@example.org", build_id="build-7",
            release_id="release-7", txt_name="_ownership.example.org", txt_value="proof-7",
        ),
        PendingClient(),
    )
    assert result.release.state == "awaiting_dns"
    assert result.user is None
