"""Settings, read once from the environment (and an optional .env file).

Every external service is optional. When its settings are missing, the matching
adapter falls back to a local implementation, so the whole pipeline runs offline
for development and tests. `Settings.services()` reports which mode each one is in.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import find_dotenv, load_dotenv


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    environment: str = "development"  # development | staging | production

    # Supabase (PostgreSQL). Empty means a local SQLite file.
    database_url: str = ""
    sqlite_path: str = "var/grc_flow.db"

    # Pinecone (index with integrated embedding). Empty api key means an in-memory index.
    pinecone_api_key: str = ""
    pinecone_index: str = "grc-dpdpa"
    pinecone_namespace: str = "dpdpa-corpus"
    pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"
    pinecone_embed_model: str = "multilingual-e5-large"

    # Upstash Redis (REST). Empty means in-process queue and cache.
    upstash_url: str = ""
    upstash_token: str = ""

    # Sentry. Empty DSN means errors are only logged.
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.1

    # Clerk. auth_mode "clerk" verifies session JWTs; "dev" trusts an X-Dev-User header
    # and is refused when environment is production.
    auth_mode: str = "clerk"
    clerk_issuer: str = ""  # e.g. https://clerk.your-domain.com
    clerk_authorized_parties: tuple[str, ...] = field(default_factory=tuple)

    # n8n. Inbound webhooks are HMAC-signed with this secret; outbound events go to the URL.
    n8n_webhook_secret: str = ""
    n8n_outbound_url: str = ""

    # Claude. "api" calls Claude; "offline" uses the deterministic stand-in extractor.
    ai_mode: str = "api"
    model: str = "claude-opus-5-5"
    effort: str = "high"

    # Corpus: the folder with manifest.json and the Act/Rules texts (GRC-Ai format).
    corpus_dir: str = "corpus"

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv(find_dotenv(usecwd=True))
        parties = tuple(p for p in _env("CLERK_AUTHORIZED_PARTIES").split(",") if p.strip())
        return cls(
            environment=_env("GRC_FLOW_ENV", cls.environment),
            database_url=_env("SUPABASE_DB_URL"),
            sqlite_path=_env("GRC_FLOW_SQLITE_PATH", cls.sqlite_path),
            pinecone_api_key=_env("PINECONE_API_KEY"),
            pinecone_index=_env("PINECONE_INDEX", cls.pinecone_index),
            pinecone_namespace=_env("PINECONE_NAMESPACE", cls.pinecone_namespace),
            pinecone_cloud=_env("PINECONE_CLOUD", cls.pinecone_cloud),
            pinecone_region=_env("PINECONE_REGION", cls.pinecone_region),
            pinecone_embed_model=_env("PINECONE_EMBED_MODEL", cls.pinecone_embed_model),
            upstash_url=_env("UPSTASH_REDIS_REST_URL"),
            upstash_token=_env("UPSTASH_REDIS_REST_TOKEN"),
            sentry_dsn=_env("SENTRY_DSN"),
            sentry_traces_sample_rate=float(_env("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            auth_mode=_env("GRC_FLOW_AUTH", cls.auth_mode),
            clerk_issuer=_env("CLERK_ISSUER").rstrip("/"),
            clerk_authorized_parties=tuple(p.strip() for p in parties),
            n8n_webhook_secret=_env("N8N_WEBHOOK_SECRET"),
            n8n_outbound_url=_env("N8N_OUTBOUND_URL"),
            ai_mode="offline" if _env("GRC_FLOW_AI_MODE").lower() == "offline" else "api",
            model=_env("GRC_FLOW_MODEL", cls.model),
            effort=_env("GRC_FLOW_EFFORT", cls.effort),
            corpus_dir=_env("GRC_FLOW_CORPUS_DIR", cls.corpus_dir),
        )

    @property
    def production(self) -> bool:
        return self.environment == "production"

    @property
    def database_target(self) -> str | Path:
        return self.database_url or Path(self.sqlite_path)

    def services(self) -> dict[str, str]:
        """Which backend each integration uses right now (shown on /health/deep)."""
        return {
            "database": "supabase" if self.database_url else "sqlite (local)",
            "vectors": "pinecone" if self.pinecone_api_key else "memory (local)",
            "queue_cache": "upstash" if self.upstash_url else "memory (local)",
            "errors": "sentry" if self.sentry_dsn else "log only",
            "auth": self.auth_mode,
            "automation": "n8n" if self.n8n_outbound_url else "disabled",
            "llm": f"claude ({self.model})" if self.ai_mode == "api" else "offline stand-in",
        }

    def validate_for_production(self) -> list[str]:
        """Problems that must block a production start. Empty means OK."""
        if not self.production:
            return []
        missing = [
            name
            for name, value in (
                ("SUPABASE_DB_URL", self.database_url),
                ("PINECONE_API_KEY", self.pinecone_api_key),
                ("UPSTASH_REDIS_REST_URL", self.upstash_url),
                ("SENTRY_DSN", self.sentry_dsn),
                ("CLERK_ISSUER", self.clerk_issuer),
                ("N8N_WEBHOOK_SECRET", self.n8n_webhook_secret),
            )
            if not value
        ]
        problems = [f"{name} is not set" for name in missing]
        if self.auth_mode != "clerk":
            problems.append("GRC_FLOW_AUTH must be 'clerk' in production")
        if self.ai_mode != "api":
            problems.append("GRC_FLOW_AI_MODE=offline is not allowed in production")
        return problems
