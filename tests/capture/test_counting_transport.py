"""CountingTransport must work as the transport of the per-request httpx.Client the Meta client opens."""
import httpx

from mirror.capture.real_run import CountingTransport


def test_counting_transport_survives_repeated_client_contexts():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    ct = CountingTransport(httpx.MockTransport(handler))
    for path in ("/v1/responses", "/v1/files", "/v1/responses"):
        with httpx.Client(base_url="https://example.test", transport=ct) as http:
            r = http.post(path, json={})
            r.read()
            assert r.status_code == 200
    assert seen == ["/v1/responses", "/v1/files", "/v1/responses"]
    assert ct.count == 2  # file uploads are not billable
    ct.close()
