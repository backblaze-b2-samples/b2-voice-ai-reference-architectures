"""The HTTP receiver: loopback by default, size cap, Authorization required."""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

from helpers import sign
from voice_session_archive import cli, server, storage, webhook


def post(port: int, body: bytes, headers: dict[str, str]) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        connection.request("POST", "/livekit", body=body, headers=headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read() or b"{}")
    finally:
        connection.close()


def test_webhook_server_on_loopback(lab):
    lab.register()
    key, size = lab.recording()
    receiver = cli.make_receiver(lab.settings, Path.cwd() / "state", lab.server_url)
    assert server.DEFAULT_HOST == "127.0.0.1"
    httpd = server.make_server(receiver, port=0)
    assert httpd.server_address[0] == "127.0.0.1"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    try:
        body = lab.body([(key, size)])
        content = {"Content-Type": "application/webhook+json"}

        status, _ = post(port, body, content)
        assert status == 401  # no Authorization

        status, _ = post(port, body, {**content, "Authorization": sign(body, secret="not-the-livekit-secret")})
        assert status == 401

        big = b"{" + b" " * (webhook.MAX_BODY_BYTES + 1) + b"}"
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        connection.putrequest("POST", "/livekit")
        connection.putheader("Content-Length", str(len(big)))
        connection.putheader("Authorization", sign(big))
        connection.endheaders()
        assert connection.getresponse().status == 413
        connection.close()

        assert lab.manifest_key not in lab.keys()
        status, result = post(port, body, {**content, "Authorization": sign(body)})
        assert status == 200 and result["status"] == "archived"
        assert storage.manifest_key(lab.room, lab.session_id) in lab.keys()

        status, result = post(port, body, {**content, "Authorization": sign(body)})
        assert status == 200 and result["status"] == "duplicate"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)
