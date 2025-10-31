
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
import os
import httpx

HA_BASE_URL = os.environ.get("HA_BASE_URL", "http://homeassistant:8123")
HA_TOKEN = os.environ.get("HA_LONG_LIVED_TOKEN", "")
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "devtoken")

class Intent(BaseModel):
    intent: str = Field(..., description="e.g., call_service")
    domain: str | None = Field(None, description="e.g., light, switch")
    service: str | None = Field(None, description="e.g., turn_on, turn_off")
    entity_id: str | None = Field(None, description="e.g., light.kitchen")
    data: dict | None = Field(default_factory=dict, description="extra service data")

app = FastAPI(title="HA Agent Bridge", version="0.1.0")

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

@app.post("/intent")
async def handle_intent(payload: Intent, authorization: str = Header(default="")):
    # Basic bearer check to keep demos private
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if token != AGENT_TOKEN:
        raise HTTPException(status_code=403, detail="Bad token")

    if payload.intent != "call_service":
        raise HTTPException(status_code=400, detail="Unsupported intent")

    if not (payload.domain and payload.service):
        raise HTTPException(status_code=400, detail="Missing domain or service")

    url = f"{HA_BASE_URL}/api/services/{payload.domain}/{payload.service}"
    headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}
    body = {}
    if payload.entity_id:
        body["entity_id"] = payload.entity_id
    if payload.data:
        body.update(payload.data)

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(url, headers=headers, json=body)
        if r.status_code >= 400:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        return {"ok": True, "response": r.json()}
