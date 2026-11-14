"""Authentication dependency for AgentProbe API."""

from __future__ import annotations

from fastapi import Header, HTTPException, status

from app.config import settings


async def verify_api_key(
    x_agentprobe_key: str | None = Header(default=None, alias="X-AgentProbe-Key"),
) -> str | None:
    """Validate incoming API key against configured environment secret.

    If AGENTPROBE_API_KEY is unset, all requests are permitted.
    """
    if not settings.API_KEY:
        return None

    if not x_agentprobe_key or x_agentprobe_key != settings.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "UNAUTHORIZED",
                "message": "Invalid or missing X-AgentProbe-Key header",
            },
        )

    return x_agentprobe_key

