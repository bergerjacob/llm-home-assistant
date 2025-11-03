
# Home Assistant Conversational Agent — Capstone Starter

Home Assistant + LLM agent + optional local stack (MQTT, Node‑RED, Ollama). Works on Docker Desktop, Codespaces, or a Raspberry Pi running Docker.

## What's inside

- `compose.yaml` — One‑command dev stack (Home Assistant, MQTT, Node‑RED, Ollama, API bridge).
- `ha/config/` — Version‑controlled Home Assistant config (safe defaults).
- `agent/` — A tiny FastAPI service that turns LLM JSON intents into Home Assistant REST calls.
- `.github/workflows/ci.yml` — CI to validate HA config, lint Python, and build images.
- `.devcontainer/` — Optional Codespaces / Dev Containers setup.
- `tests/` — Golden intent examples and a contract test for the agent API.
- `.env.example` — Copy to `.env` and fill in secrets locally (never commit `.env`).

## Quick start (local Docker)

1) **Clone** and create env:
```bash
cp .env.example .env
# edit .env and set HA_BASE_URL after first run; keep AGENT_TOKEN random
```

2) **Launch stack:**
```bash
docker compose up -d
```

3) **Finish HA onboarding:**
- Visit http://localhost:8123 and create an admin account.
- In HA: Settings → People → your user → Create Long-Lived Access Token. Paste into `.env` as `HA_LONG_LIVED_TOKEN`.

4) **Test the agent:**
```bash
curl -X POST http://localhost:8091/intent   -H "Authorization: Bearer $AGENT_TOKEN"   -H "Content-Type: application/json"   -d '{"intent":"call_service","domain":"light","service":"turn_on","entity_id":"light.kitchen"}'
```

5) **(Optional) Local LLM via Ollama:**
- `docker compose exec ollama ollama pull llama3.1`
- POST to your LLM router (future step) or call directly from `agent/` if desired.

## Deploy to Raspberry Pi

Install Docker and docker-compose plugin, then:
```bash
git clone <your-repo>
cd <your-repo>
cp .env.example .env  # fill values
docker compose up -d
```

Docker will pull the correct ARM images for Pi automatically if available.

## CI

- **HA config check**: runs Home Assistant's `--script check_config`
- **Python lint/type**: Ruff + MyPy
- **Build**: builds `agent` image to ensure Dockerfile stays healthy

## Repo layout

```
.
├── .devcontainer/
├── .github/workflows/ci.yml
├── agent/
│   ├── app.py
│   ├── requirements.txt
│   ├── Dockerfile
│   └── tests/
├── ha/
│   └── config/
│       ├── configuration.yaml
│       ├── intents.yaml
│       └── secrets.yaml.example
├── tests/
│   └── intents/
│       └── turn_on_kitchen_light.json
├── compose.yaml
├── .env.example
└── README.md
```

## Secrets

- `.env` is **ignored** by git. Use GitHub Actions **Secrets** for CI/CD if needed.

## Next steps

- Add your **Speech-to-Text** (e.g., Whisper) and **NLU** layer to map phrases → JSON intents.
- Expand `agent/` with safer validation, allow/deny lists, and dry‑run mode for demos.
- Add device/entity fixtures in HA and build end‑to‑end tests that simulate a few intents.
