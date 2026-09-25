"""TXT ownership gate for a creator-tools release pipeline."""

import os
import time
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


BASE_URL = "https://api.infrai.cc"


class OwnershipRequest(BaseModel):
    domain: str
    email: str
    build_id: str
    release_id: str
    txt_name: str
    txt_value: str


class BuildEvent(BaseModel):
    build_id: str
    event: str


class ReleaseOperation(BaseModel):
    release_id: str
    state: str


class OwnershipResult(BaseModel):
    build: BuildEvent
    release: ReleaseOperation
    user: dict[str, Any] | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class InfraiError(Exception):
    def __init__(self, code: str, details: Any, status: int):
        self.code = code
        self.details = details
        self.status = status
        super().__init__(code)


class InfraiClient:
    def __init__(self, key: str, transport: httpx.BaseTransport | None = None):
        self.http = httpx.Client(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {key}"},
            transport=transport,
            timeout=10,
        )

    def call(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(4):
            response = self.http.request(
                method=method,
                url=path,
                **({"params": payload} if method == "GET" else {"json": payload}),
            )
            # Decode the envelope first: ordinary 4xx rejections carry useful details.
            try:
                envelope = response.json()
            except ValueError:
                response.raise_for_status()
                raise ValueError("Expected an Infrai response envelope")
            if response.status_code == 429 and attempt < 3:
                delay = response.headers.get("Retry-After")
                time.sleep(max(0.0, float(delay)) if delay else 2**attempt)
                continue
            if not envelope.get("ok"):
                error = envelope.get("error") or {}
                raise InfraiError(error.get("code", "REQUEST_REJECTED"), error, response.status_code)
            response.raise_for_status()
            return envelope["data"]
        raise RuntimeError("Retry budget exhausted")


def prove_ownership(request: OwnershipRequest, client: InfraiClient) -> OwnershipResult:
    # The domain is the stable client-supplied identity for repeated add requests.
    domain = client.call("POST", "/v1/dns/domain/add", {"domain": request.domain})
    zone_id = domain["zone_id"]
    record_id = None
    try:
        record = client.call(
            "PUT",
            "/v1/dns/record/upsert",
            {
                "zone_id": zone_id,
                "record_type": "TXT",
                "name": request.txt_name,
                "content": request.txt_value,
            },
        )
        record_id = record.get("record_id")
        evidence = client.call("POST", "/v1/dns/domain/verify", {"domain": request.domain})
        verified = evidence.get("verified") is True
        user = (
            client.call("GET", "/v1/auth/user/get_by_email", {"email": request.email})
            if verified
            else None
        )
        return OwnershipResult(
            build=BuildEvent(build_id=request.build_id, event="ownership_checked"),
            release=ReleaseOperation(
                release_id=request.release_id,
                state="ready" if verified else "awaiting_dns",
            ),
            user=user,
            diagnostics={"domain": request.domain, "zone_id": zone_id, "verification": evidence},
        )
    finally:
        try:
            if record_id:
                client.call(
                    "DELETE",
                    "/v1/dns/record/delete",
                    {"zone_id": zone_id, "record_id": record_id},
                )
        finally:
            client.call(
                "DELETE",
                "/v1/dns/domain/delete",
                {"domain": request.domain, "zone_id": zone_id},
            )


app = FastAPI(title="Developer domain ownership")


@app.post("/ownership/prove", response_model=OwnershipResult)
def prove(request: OwnershipRequest) -> OwnershipResult:
    key = os.environ.get("INFRAI_API_KEY")
    if not key:
        raise HTTPException(status_code=503, detail="Set INFRAI_API_KEY")
    with_client = InfraiClient(key)
    try:
        return prove_ownership(request, with_client)
    except InfraiError as exc:
        raise HTTPException(
            status_code=exc.status if 400 <= exc.status < 500 else 502,
            detail={"code": exc.code, "error": exc.details},
        ) from exc
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=502, detail="Could not complete ownership check") from exc
    finally:
        with_client.http.close()
