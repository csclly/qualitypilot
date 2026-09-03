"""Local-only Uvicorn smoke test for RS256 OIDC authentication and RBAC."""

from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm


BACKEND_ROOT = Path(__file__).parents[1]
TEST_DATABASE_URL = (
    "postgresql+asyncpg://qualitypilot_test:qualitypilot_test_password@"
    "127.0.0.1:5433/qualitypilot_test"
)


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class JwksHandler(BaseHTTPRequestHandler):
    payload = b"{}"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/jwks.json":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    jwks_port = _free_port()
    api_port = _free_port()
    issuer = f"http://127.0.0.1:{jwks_port}"
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    public_jwk.update({"kid": "smoke-key-1", "alg": "RS256", "use": "sig"})
    JwksHandler.payload = json.dumps({"keys": [public_jwk]}).encode("utf-8")
    jwks_server = ThreadingHTTPServer(("127.0.0.1", jwks_port), JwksHandler)
    jwks_thread = threading.Thread(target=jwks_server.serve_forever, daemon=True)
    jwks_thread.start()

    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": TEST_DATABASE_URL,
            "ENVIRONMENT": "test",
            "DEBUG": "false",
            "DASHSCOPE_API_KEY": "",
            "AGENT_APPROVAL_AUTH_REQUIRED": "true",
            "AGENT_APPROVAL_API_KEY_SHA256": "",
            "AGENT_APPROVAL_ACTOR_ID": "",
            "AGENT_OIDC_ENABLED": "true",
            "AGENT_OIDC_ISSUER": issuer,
            "AGENT_OIDC_AUDIENCE": "qualitypilot-api",
            "AGENT_OIDC_JWKS_URL": f"{issuer}/jwks.json",
            "AGENT_OIDC_LEEWAY_SECONDS": "0",
            "AGENT_ALERT_SCHEDULER_ENABLED": "false",
        }
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(api_port),
            "--log-level",
            "warning",
        ],
        cwd=BACKEND_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        base_url = f"http://127.0.0.1:{api_port}"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout is not None else ""
                raise RuntimeError(f"Uvicorn 启动失败: {output}")
            try:
                if httpx.get(f"{base_url}/api/v1/ready", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("Uvicorn 就绪超时")

        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "iss": issuer,
                "aud": "qualitypilot-api",
                "sub": "oidc-smoke-reader",
                "roles": ["retention_reader"],
                "iat": now,
                "exp": now + timedelta(minutes=2),
            },
            private_key,
            algorithm="RS256",
            headers={"kid": "smoke-key-1"},
        )
        headers = {"Authorization": f"Bearer {token}"}
        preview = httpx.get(
            f"{base_url}/api/v1/agent/retention/preview",
            headers=headers,
            timeout=5,
        )
        metrics = httpx.get(
            f"{base_url}/api/v1/agent/metrics",
            headers=headers,
            timeout=5,
        )
        viewer_token = jwt.encode(
            {
                "iss": issuer,
                "aud": "qualitypilot-api",
                "sub": "oidc-smoke-metrics-reader",
                "roles": ["alert_viewer"],
                "iat": now,
                "exp": now + timedelta(minutes=2),
            },
            private_key,
            algorithm="RS256",
            headers={"kid": "smoke-key-1"},
        )
        prometheus = httpx.get(
            f"{base_url}/api/v1/observability/metrics",
            headers={"Authorization": f"Bearer {viewer_token}"},
            timeout=5,
        )
        if (
            preview.status_code != 200
            or metrics.status_code != 403
            or prometheus.status_code != 200
            or "qualitypilot_http_requests_total" not in prometheus.text
            or 'route="/api/v1/ready"' not in prometheus.text
        ):
            raise RuntimeError(
                "OIDC 冒烟失败: "
                f"preview={preview.status_code}, metrics={metrics.status_code}, "
                f"prometheus={prometheus.status_code}"
            )
        print(
            json.dumps(
                {
                    "ready": 200,
                    "oidc_retention_reader": preview.status_code,
                    "rbac_metrics_denied": metrics.status_code,
                    "prometheus_metrics": prometheus.status_code,
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        jwks_server.shutdown()
        jwks_server.server_close()
        jwks_thread.join(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
