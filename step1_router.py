import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .models.openai.call_openai import async_query_openai

_LOGGER = logging.getLogger(__name__)


def _safe_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, str):
        try:
            parsed = json.loads(obj)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    out: list[str] = []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _build_ha_summary(hass: HomeAssistant, max_entities_per_domain: int) -> dict[str, Any]:
    services = hass.services.async_services() or {}
    domains = sorted(services.keys())

    entities_by_domain: dict[str, list[str]] = {}
    for st in hass.states.async_all():
        dom = st.entity_id.split(".", 1)[0]
        bucket = entities_by_domain.setdefault(dom, [])
        if len(bucket) < max_entities_per_domain:
            bucket.append(st.entity_id)

    return {
        "domains_present": sorted(set(list(entities_by_domain.keys()) + domains)),
        "example_entities_by_domain": entities_by_domain,
        "example_services_by_domain": {
            d: sorted(list((services.get(d) or {}).keys()))[:20] for d in domains
        },
    }


async def step1_route_candidates(
    hass: HomeAssistant,
    session,
    api_key: str,
    user_text: str,
    *,
    max_candidates: int = 20,
    max_entities_per_domain: int = 12,
) -> dict[str, Any]:
    """
    Returns {"candidate_entities": [...], "candidate_services": [...]}.
    On any failure, returns empty lists so Step 2 can fall back to original behavior.
    """
    try:
        ha_summary = _build_ha_summary(hass, max_entities_per_domain=max_entities_per_domain)

        system_prompt = (
            "You are a cheap routing model for Home Assistant.\n"
            "Return STRICT JSON only:\n"
            '{ "candidate_entities": ["domain.entity", ...], "candidate_services": ["domain.service", ...] }\n'
            f"Keep lists <= {max_candidates} each. Use only what plausibly exists in the HA summary. JSON only."
        )

        payload = {"user_command": user_text, "ha_summary": ha_summary}
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload)},
        ]

        try:
            reply = await async_query_openai(
                hass=hass,
                session=session,
                api_key=api_key,
                messages=messages,
                model="gpt-5-nano",
            )
        except TypeError:
            reply = await async_query_openai(
                hass=hass,
                session=session,
                api_key=api_key,
                messages=messages,
            )

        data = _safe_dict(reply)

        candidate_entities = _dedupe(data.get("candidate_entities") or [])[:max_candidates]
        candidate_services = _dedupe(data.get("candidate_services") or [])[:max_candidates]

        candidate_entities = [eid for eid in candidate_entities if hass.states.get(eid) is not None]

        return {
            "candidate_entities": candidate_entities,
            "candidate_services": candidate_services,
        }

    except Exception as exc:
        _LOGGER.warning("Step1 routing failed; returning empty candidates: %s", exc)
        return {"candidate_entities": [], "candidate_services": []}