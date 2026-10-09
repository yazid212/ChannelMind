import os
import ipaddress
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlparse


def _csv(name: str, default: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, default).split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development").lower()
    app_secret: str = os.getenv("APP_SECRET", "development-only-change-me")
    api_prefix: str = "/api/v1"
    allowed_origins: tuple[str, ...] = _csv(
        "ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    )
    allowed_hosts: tuple[str, ...] = _csv("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
    redis_url: str | None = os.getenv("REDIS_URL")
    export_store_path: str = os.getenv("EXPORT_STORE_PATH", "data/channel-exports.sqlite3")
    trust_proxy_headers: bool = os.getenv("TRUST_PROXY_HEADERS", "false").lower() == "true"
    trusted_proxy_cidrs: tuple[str, ...] = _csv("TRUSTED_PROXY_CIDRS", "")
    workspace_ttl_seconds: int = int(os.getenv("WORKSPACE_TTL_SECONDS", "86400"))
    max_request_bytes: int = int(os.getenv("MAX_REQUEST_BYTES", "32768"))
    google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "")
    google_client_secret: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    extension_ids: tuple[str, ...] = _csv("CHANNELMIND_EXTENSION_IDS", "")
    export_retention_seconds: int = int(os.getenv("EXPORT_RETENTION_SECONDS", "86400"))
    enable_transcript_cache: bool = (
        os.getenv("ENABLE_TRANSCRIPT_CACHE", "false").lower() == "true"
    )

    @property
    def production(self) -> bool:
        return self.app_env == "production"

    def validate(self) -> None:
        problems = []
        if self.production and (len(self.app_secret) < 32 or self.app_secret in {
            "development-only-change-me",
            "local-development-secret-change-before-deploying",
        }):
            problems.append("APP_SECRET must be a unique secret of at least 32 characters in production")
        if self.google_client_id and len(self.app_secret) < 32:
            problems.append("APP_SECRET must be at least 32 random characters when Google OAuth is enabled")
        if self.production and not self.redis_url:
            problems.append("REDIS_URL is required in production")
        if self.trust_proxy_headers and not self.trusted_proxy_cidrs:
            problems.append("TRUSTED_PROXY_CIDRS is required when TRUST_PROXY_HEADERS=true")
        for cidr in self.trusted_proxy_cidrs:
            try:
                ipaddress.ip_network(cidr, strict=False)
            except ValueError:
                problems.append("TRUSTED_PROXY_CIDRS contains an invalid network")
        if self.production and os.getenv("VECTOR_BACKEND", "chroma").lower() != "qdrant":
            problems.append("VECTOR_BACKEND=qdrant is required in production")
        if self.production and not os.getenv("QDRANT_URL"):
            problems.append("QDRANT_URL is required in production")
        if self.production and not os.getenv("QDRANT_API_KEY"):
            problems.append("QDRANT_API_KEY is required in production")
        if self.production and not os.getenv("YOUTUBE_API_KEY"):
            problems.append("YOUTUBE_API_KEY is required for the channel workflow in production")
        if self.production:
            provider = os.getenv("LLM_PROVIDER", "openrouter").lower()
            key_name = {
                "openrouter": "OPENROUTER_API_KEY",
                "groq": "GROQ_API_KEY",
                "grok": "GROK_API_KEY",
                "nvidia": "NVIDIA_API_KEY",
            }.get(provider)
            if not key_name:
                problems.append("LLM_PROVIDER must name a supported provider")
            elif not os.getenv(key_name):
                problems.append(f"{key_name} is required in production")
            if not self.allowed_origins or any(
                (parsed := urlparse(origin)).scheme != "https"
                or not parsed.hostname
                or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment or "*" in origin
                or parsed.hostname in {"localhost", "127.0.0.1"}
                for origin in self.allowed_origins
            ):
                problems.append("ALLOWED_ORIGINS must contain explicit HTTPS site origins in production")
            if not self.allowed_hosts or any(
                host in {"*", "localhost", "127.0.0.1", "testserver"}
                or "://" in host or "/" in host
                for host in self.allowed_hosts
            ):
                problems.append("ALLOWED_HOSTS must contain explicit public API hostnames in production")
        if any(len(item) != 32 or any(c not in "abcdefghijklmnop" for c in item) for item in self.extension_ids):
            problems.append("CHANNELMIND_EXTENSION_IDS must contain valid Chrome extension IDs")
        if self.export_retention_seconds < 300:
            problems.append("EXPORT_RETENTION_SECONDS must be at least 300")
        if problems:
            raise RuntimeError("; ".join(problems))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
