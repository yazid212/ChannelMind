"""Signed anonymous workspace capabilities and request identity helpers."""

import base64
import hashlib
import hmac
import ipaddress
import json
import time
import uuid
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request, status

from .settings import get_settings


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


@dataclass(frozen=True)
class WorkspaceIdentity:
    workspace_id: str
    expires_at: int


def issue_workspace_token() -> tuple[WorkspaceIdentity, str]:
    settings = get_settings()
    identity = WorkspaceIdentity(
        workspace_id=str(uuid.uuid4()),
        expires_at=int(time.time()) + settings.workspace_ttl_seconds,
    )
    return identity, sign_workspace_token(identity)


def sign_workspace_token(identity: WorkspaceIdentity) -> str:
    """Sign an existing identity for a one-time extension-to-web handoff."""
    settings = get_settings()
    payload = _encode(
        json.dumps(
            {"sub": identity.workspace_id, "exp": identity.expires_at, "v": 1},
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signature = _encode(
        hmac.new(settings.app_secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{payload}.{signature}"


def verify_workspace_token(token: str) -> WorkspaceIdentity:
    settings = get_settings()
    try:
        payload, supplied_signature = token.split(".", 1)
        expected_signature = _encode(
            hmac.new(
                settings.app_secret.encode("utf-8"),
                payload.encode("ascii"),
                hashlib.sha256,
            ).digest()
        )
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ValueError("signature mismatch")
        claims = json.loads(_decode(payload))
        workspace_id = str(uuid.UUID(claims["sub"]))
        expires_at = int(claims["exp"])
        if claims.get("v") != 1 or expires_at <= int(time.time()):
            raise ValueError("expired token")
        return WorkspaceIdentity(workspace_id=workspace_id, expires_at=expires_at)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired workspace token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None


def require_workspace(authorization: str | None = Header(default=None)) -> WorkspaceIdentity:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Workspace token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return verify_workspace_token(authorization[7:].strip())


def client_address(request: Request) -> str:
    settings = get_settings()
    peer = request.client.host if request.client else "unknown"
    if settings.trust_proxy_headers and settings.trusted_proxy_cidrs:
        try:
            trusted = any(
                ipaddress.ip_address(peer) in ipaddress.ip_network(cidr, strict=False)
                for cidr in settings.trusted_proxy_cidrs
            )
            if trusted:
                # Railway's HTTP edge supplies X-Real-IP; other trusted reverse
                # proxies may supply X-Forwarded-For instead.
                real_ip = request.headers.get("x-real-ip", "").strip()
                if real_ip:
                    ipaddress.ip_address(real_ip)
                    return real_ip
                forwarded = request.headers.get("x-forwarded-for", "").rsplit(",", 1)[-1].strip()
                ipaddress.ip_address(forwarded)
                return forwarded
        except ValueError:
            pass
    return peer
