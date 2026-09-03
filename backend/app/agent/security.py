import asyncio
import hashlib
import hmac
from dataclasses import dataclass
from typing import Protocol

import jwt
from jwt import PyJWK, PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientError

from app.core.config import Settings


APPROVER_ROLE = "quality_approver"
ALERT_OPERATOR_ROLE = "alert_operator"
ALERT_VIEWER_ROLE = "alert_viewer"
RETENTION_OPERATOR_ROLE = "retention_operator"
RETENTION_READER_ROLE = "retention_reader"
AGENT_ADMIN_ROLE = "agent_admin"
KNOWN_AGENT_ROLES = frozenset(
    {
        APPROVER_ROLE,
        ALERT_OPERATOR_ROLE,
        ALERT_VIEWER_ROLE,
        RETENTION_OPERATOR_ROLE,
        RETENTION_READER_ROLE,
        AGENT_ADMIN_ROLE,
    }
)
API_KEY_AUTH_METHOD = "api_key_sha256"
OIDC_AUTH_METHOD = "oidc_jwt_rs256"


class ApprovalAuthenticationError(RuntimeError):
    pass


class OidcSigningKeyResolver(Protocol):
    async def resolve(self, token: str) -> PyJWK: ...


class PyJwkClientSigningKeyResolver:
    """在工作线程中解析远端 JWKS，避免阻塞 FastAPI 事件循环。"""

    def __init__(self, settings: Settings) -> None:
        if settings.agent_oidc_jwks_url is None:
            raise ValueError("OIDC JWKS URL 未配置")
        self._client = PyJWKClient(
            settings.agent_oidc_jwks_url,
            cache_keys=True,
            cache_jwk_set=True,
            lifespan=settings.agent_oidc_jwks_cache_seconds,
            timeout=settings.agent_oidc_http_timeout_seconds,
        )

    async def resolve(self, token: str) -> PyJWK:
        return await asyncio.to_thread(
            self._client.get_signing_key_from_jwt,
            token,
        )


@dataclass(frozen=True, slots=True)
class ApprovalPrincipal:
    actor_id: str
    roles: frozenset[str]
    authenticated: bool
    auth_method: str | None

    def has_any_role(self, *roles: str) -> bool:
        return AGENT_ADMIN_ROLE in self.roles or not self.roles.isdisjoint(roles)

    @property
    def can_approve(self) -> bool:
        return self.has_any_role(APPROVER_ROLE)

    @property
    def can_operate_alerts(self) -> bool:
        return self.has_any_role(ALERT_OPERATOR_ROLE)

    @property
    def can_view_alerts(self) -> bool:
        return self.has_any_role(ALERT_OPERATOR_ROLE, ALERT_VIEWER_ROLE)

    @property
    def can_manage_retention(self) -> bool:
        return self.has_any_role(RETENTION_OPERATOR_ROLE)

    @property
    def can_view_retention(self) -> bool:
        return self.has_any_role(RETENTION_OPERATOR_ROLE, RETENTION_READER_ROLE)


class OidcJwtAuthenticator:
    def __init__(
        self,
        settings: Settings,
        resolver: OidcSigningKeyResolver,
    ) -> None:
        if not settings.agent_oidc_enabled:
            raise ValueError("OIDC 未启用")
        self._issuer = settings.agent_oidc_issuer or ""
        self._audience = settings.agent_oidc_audience or ""
        self._roles_claim = settings.agent_oidc_roles_claim
        self._leeway_seconds = settings.agent_oidc_leeway_seconds
        self._resolver = resolver

    async def authenticate(self, token: str) -> ApprovalPrincipal:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256":
                raise ApprovalAuthenticationError("OIDC 令牌算法不受支持")
            if not isinstance(header.get("kid"), str) or not header["kid"].strip():
                raise ApprovalAuthenticationError("OIDC 令牌缺少密钥标识")
            signing_key = await self._resolver.resolve(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway_seconds,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except ApprovalAuthenticationError:
            raise
        except (InvalidTokenError, PyJWKClientError, OSError, ValueError) as exc:
            raise ApprovalAuthenticationError("OIDC 令牌验证失败") from exc

        subject = claims.get("sub")
        if (
            not isinstance(subject, str)
            or not subject.strip()
            or subject != subject.strip()
            or len(subject) > 255
        ):
            raise ApprovalAuthenticationError("OIDC 令牌主体无效")
        raw_roles = claims.get(self._roles_claim, [])
        if not isinstance(raw_roles, list) or any(
            not isinstance(role, str) for role in raw_roles
        ):
            raise ApprovalAuthenticationError("OIDC 角色声明格式无效")
        roles = frozenset(raw_roles).intersection(KNOWN_AGENT_ROLES)
        return ApprovalPrincipal(
            actor_id=subject,
            roles=roles,
            authenticated=True,
            auth_method=OIDC_AUTH_METHOD,
        )


def build_oidc_authenticator(settings: Settings) -> OidcJwtAuthenticator | None:
    if not settings.agent_oidc_enabled:
        return None
    return OidcJwtAuthenticator(
        settings,
        PyJwkClientSigningKeyResolver(settings),
    )


def authenticate_approval_principal(
    authorization: str | None,
    settings: Settings,
) -> ApprovalPrincipal:
    """兼容既有 API Key 的同步认证路径。"""

    token = _bearer_token(authorization, settings)
    if token is None:
        return _unverified_principal()
    return _authenticate_api_key(token, settings)


async def authenticate_agent_principal(
    authorization: str | None,
    settings: Settings,
    oidc_authenticator: OidcJwtAuthenticator | None,
) -> ApprovalPrincipal:
    token = _bearer_token(authorization, settings)
    if token is None:
        return _unverified_principal()
    if settings.agent_oidc_enabled and token.count(".") == 2:
        if oidc_authenticator is None:
            raise ApprovalAuthenticationError("OIDC 认证尚未初始化")
        return await oidc_authenticator.authenticate(token)
    return _authenticate_api_key(token, settings)


def _bearer_token(
    authorization: str | None,
    settings: Settings,
) -> str | None:
    if authorization is None:
        if settings.agent_approval_auth_required:
            raise ApprovalAuthenticationError("Agent 接口需要 Bearer 凭证")
        return None
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not token.strip():
        raise ApprovalAuthenticationError("Authorization 必须使用 Bearer 凭证")
    if token != token.strip():
        raise ApprovalAuthenticationError("Bearer 凭证格式无效")
    return token


def _authenticate_api_key(token: str, settings: Settings) -> ApprovalPrincipal:
    configured_digest = settings.agent_approval_api_key_sha256
    actor_id = settings.agent_approval_actor_id
    if configured_digest is None or actor_id is None:
        raise ApprovalAuthenticationError("API Key 认证尚未配置")
    expected = configured_digest.get_secret_value().strip().lower()
    actual = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(actual, expected):
        raise ApprovalAuthenticationError("Agent API Key 无效")
    return ApprovalPrincipal(
        actor_id=actor_id,
        roles=settings.agent_approval_api_key_role_set.intersection(
            KNOWN_AGENT_ROLES
        ),
        authenticated=True,
        auth_method=API_KEY_AUTH_METHOD,
    )


def _unverified_principal() -> ApprovalPrincipal:
    return ApprovalPrincipal(
        actor_id="unverified",
        roles=frozenset(),
        authenticated=False,
        auth_method=None,
    )
