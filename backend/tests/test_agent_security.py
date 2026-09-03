import hashlib
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWK
from jwt.algorithms import RSAAlgorithm
from pydantic import ValidationError

from app.agent.security import (
    ALERT_OPERATOR_ROLE,
    API_KEY_AUTH_METHOD,
    APPROVER_ROLE,
    OIDC_AUTH_METHOD,
    RETENTION_READER_ROLE,
    ApprovalAuthenticationError,
    OidcJwtAuthenticator,
    authenticate_approval_principal,
)
from app.core.config import Settings


class StaticSigningKeyResolver:
    def __init__(self, key: PyJWK) -> None:
        self.key = key
        self.calls = 0

    async def resolve(self, token: str) -> PyJWK:
        assert token
        self.calls += 1
        return self.key


def _oidc_fixture() -> tuple[Settings, object, StaticSigningKeyResolver]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    jwk.update({"kid": "test-key-1", "alg": "RS256", "use": "sig"})
    settings = Settings(
        _env_file=None,
        environment="test",
        agent_approval_auth_required=True,
        agent_oidc_enabled=True,
        agent_oidc_issuer="https://issuer.example.test",
        agent_oidc_audience="qualitypilot-api",
        agent_oidc_jwks_url="https://issuer.example.test/.well-known/jwks.json",
        agent_oidc_leeway_seconds=0,
    )
    return settings, private_key, StaticSigningKeyResolver(PyJWK.from_dict(jwk))


def _token(private_key: object, **overrides: object) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": "https://issuer.example.test",
        "aud": "qualitypilot-api",
        "sub": "quality-engineer-oidc",
        "roles": [APPROVER_ROLE, RETENTION_READER_ROLE, "unknown_role"],
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key-1"},
    )


def test_optional_auth_returns_explicit_unverified_principal() -> None:
    principal = authenticate_approval_principal(None, Settings(_env_file=None))

    assert principal.actor_id == "unverified"
    assert principal.authenticated is False
    assert principal.auth_method is None
    assert principal.can_approve is False


def test_valid_bearer_key_returns_configured_roles() -> None:
    api_key = "test-approval-key-with-sufficient-randomness"
    settings = Settings(
        _env_file=None,
        agent_approval_auth_required=True,
        agent_approval_api_key_sha256=hashlib.sha256(
            api_key.encode("utf-8")
        ).hexdigest(),
        agent_approval_actor_id="quality-engineer-001",
    )

    principal = authenticate_approval_principal(f"Bearer {api_key}", settings)

    assert principal.actor_id == "quality-engineer-001"
    assert {APPROVER_ROLE, ALERT_OPERATOR_ROLE}.issubset(principal.roles)
    assert principal.authenticated is True
    assert principal.auth_method == API_KEY_AUTH_METHOD


@pytest.mark.parametrize(
    "authorization",
    [None, "Basic abc", "Bearer wrong-key", "Bearer ", "Bearer key "],
)
def test_required_auth_rejects_missing_or_invalid_credentials(
    authorization: str | None,
) -> None:
    settings = Settings(
        _env_file=None,
        agent_approval_auth_required=True,
        agent_approval_api_key_sha256="0" * 64,
        agent_approval_actor_id="quality-engineer-001",
    )

    with pytest.raises(ApprovalAuthenticationError):
        authenticate_approval_principal(authorization, settings)


async def test_valid_oidc_token_returns_filtered_roles_and_subject() -> None:
    settings, private_key, resolver = _oidc_fixture()
    principal = await OidcJwtAuthenticator(settings, resolver).authenticate(
        _token(private_key)
    )

    assert principal.actor_id == "quality-engineer-oidc"
    assert principal.roles == frozenset({APPROVER_ROLE, RETENTION_READER_ROLE})
    assert principal.auth_method == OIDC_AUTH_METHOD
    assert principal.authenticated is True
    assert resolver.calls == 1


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"aud": "wrong-api"}, "验证失败"),
        ({"iss": "https://wrong.example.test"}, "验证失败"),
        ({"exp": datetime.now(UTC) - timedelta(seconds=1)}, "验证失败"),
        ({"sub": "  "}, "主体无效"),
        ({"roles": APPROVER_ROLE}, "角色声明格式无效"),
    ],
)
async def test_oidc_rejects_invalid_claims(
    overrides: dict[str, object],
    message: str,
) -> None:
    settings, private_key, resolver = _oidc_fixture()
    with pytest.raises(ApprovalAuthenticationError, match=message):
        await OidcJwtAuthenticator(settings, resolver).authenticate(
            _token(private_key, **overrides)
        )


async def test_oidc_rejects_algorithm_before_key_resolution() -> None:
    settings, _, resolver = _oidc_fixture()
    token = jwt.encode(
        {"sub": "actor"},
        "local-test-secret-with-at-least-32-bytes",
        algorithm="HS256",
        headers={"kid": "test-key-1"},
    )
    with pytest.raises(ApprovalAuthenticationError, match="算法不受支持"):
        await OidcJwtAuthenticator(settings, resolver).authenticate(token)
    assert resolver.calls == 0


def test_settings_reject_invalid_or_incomplete_auth() -> None:
    with pytest.raises(ValidationError, match="64 位十六进制"):
        Settings(_env_file=None, agent_approval_api_key_sha256="not-a-digest")

    with pytest.raises(ValidationError, match="强制 Agent 认证"):
        Settings(_env_file=None, agent_approval_auth_required=True)

    with pytest.raises(ValidationError, match="启用 OIDC"):
        Settings(_env_file=None, agent_oidc_enabled=True)

    with pytest.raises(ValidationError, match="HTTPS"):
        Settings(
            _env_file=None,
            environment="production",
            agent_approval_auth_required=True,
            agent_oidc_enabled=True,
            agent_oidc_issuer="https://issuer.example.test",
            agent_oidc_audience="qualitypilot-api",
            agent_oidc_jwks_url="http://issuer.example.test/jwks.json",
        )
