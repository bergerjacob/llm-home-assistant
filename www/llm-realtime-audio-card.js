console.log("LLM Realtime Audio Card: Loading...");

const RESPONSE_ENTITY = "sensor.llm_model_response";

class LLMRealtimeAudioCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._initialized = false;
    this._processing = false;
    this._lastResponse = "";
  }

  setConfig(config) {
    this.config = { entities: [RESPONSE_ENTITY], response_entity: RESPONSE_ENTITY, ...config };
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._initialized) {
      this._initialized = true;
      this.render();
    }
    this._updateFromHass();
  }

  _updateFromHass() {
    if (!this._hass || !this._initialized) return;
    const statusLine = this.shadowRoot.getElementById("statusLine");
    const outputText = this.shadowRoot.getElementById("outputText");
    if (!statusLine || !outputText) return;

    const entityId = this.config.response_entity || RESPONSE_ENTITY;
    const state = this._hass.states[entityId];
    const fullText = state?.attributes?.full_text || state?.state || "";
    const text = fullText ? String(fullText) : "";

    if (this._processing) {
      if (text && text !== this._lastResponse) {
        this._processing = false;
        if (this._processingTickId) {
          clearTimeout(this._processingTickId);
          this._processingTickId = null;
        }
        this._lastResponse = text;
        statusLine.textContent = "Done";
        statusLine.className = "status-line";
        outputText.textContent = text;
        outputText.className = "output-text";
      } else {
        statusLine.textContent = "Processing (Realtime API)…";
        statusLine.className = "status-line processing";
        outputText.textContent = text || "—";
        outputText.className = text ? "output-text" : "output-text muted";
      }
    } else {
      statusLine.className = "status-line";
      statusLine.textContent = "Ready";
      outputText.textContent = text || "—";
      outputText.className = text ? "output-text" : "output-text muted";
      if (text) this._lastResponse = text;
    }
  }

  render() {
    const card = document.createElement("ha-card");
    card.header = this.config.title || "Unified Audio + Tool Calling (Realtime)";

    const content = document.createElement("div");
    content.style.padding = "16px";

    content.innerHTML = `
      <style>
        .card-subtitle { font-size: 12px; color: var(--secondary-text-color, #666); margin: -8px 0 12px 0; }
        .row { margin: 15px 0; }
        label { display: block; margin-bottom: 5px; font-weight: bold; }
        select, button { width: 100%; padding: 10px; margin: 5px 0; font-size: 16px; }
        .toggle-btn { background: #1565c0; color: white; border: none; border-radius: 4px; cursor: pointer; }
        .toggle-btn.recording { background: #c62828; }
        .status-box {
          display: block;
          margin-top: 16px;
          min-height: 88px;
          padding: 12px 14px;
          background: var(--card-background-color, #f5f5f5);
          border: 1px solid var(--divider-color, #b0b0b0);
          border-radius: 6px;
          box-sizing: border-box;
        }
        .status-line { font-size: 14px; font-weight: 600; margin-bottom: 8px; color: var(--primary-text-color, #333); }
        .status-line.processing { color: #1565c0; font-style: italic; }
        .output-text { font-size: 14px; line-height: 1.45; white-space: pre-wrap; word-break: break-word; color: var(--primary-text-color, #111); }
        .output-text.muted { color: var(--secondary-text-color, #666); }
      </style>
      <div class="card-subtitle">Voice → Realtime API → tools run in HA</div>
      <div class="row">
        <button id="toggleBtn" class="toggle-btn">Start Recording</button>
      </div>
      <div class="row">
        <label for="modelSelect">Model:</label>
        <select id="modelSelect">
          <option value="openai">OpenAI (Realtime)</option>
        </select>
      </div>
      <div class="status-box" id="statusBox">
        <div class="status-line" id="statusLine">Ready</div>
        <div class="output-text muted" id="outputText">—</div>
      </div>
    `;

    card.appendChild(content);
    this.shadowRoot.appendChild(card);
    this.content = content;

    this.shadowRoot.getElementById("toggleBtn").addEventListener("click", () => this.toggleRecording());
    this._updateFromHass();
  }

  toggleRecording() {
    const btn = this.shadowRoot.getElementById("toggleBtn");
    const isRecording = btn.classList.contains("recording");
    if (isRecording) {
      this._hass
        .callService("llm_home_assistant", "stop_recording", { pipeline: "realtime" })
        .then(() => {
          btn.classList.remove("recording");
          btn.textContent = "Start Recording";
          this._processing = true;
          this._updateFromHass();
          setTimeout(() => {
            this._hass
              .callService("llm_home_assistant", "process_realtime_audio", {
                filename: "current_request.wav",
              })
              .then(() => {
                this._processingTick();
              })
              .catch((err) => {
                console.error("process_realtime_audio error:", err);
                this._processing = false;
                this._updateFromHass();
              });
          }, 1500);
        })
        .catch((err) => {
          console.error("Error stopping recording:", err);
          this._processing = false;
          this._updateFromHass();
        });
    } else {
      this._hass
        .callService("llm_home_assistant", "start_recording", {})
        .then(() => {
          btn.classList.add("recording");
          btn.textContent = "Stop Recording";
          this._updateFromHass();
        })
        .catch((err) => {
          console.error("Error starting recording:", err);
        });
    }
  }

  _processingTick() {
    if (!this._processing) return;
    this._updateFromHass();
    this._processingTickId = setTimeout(() => this._processingTick(), 500);
  }

  getCardSize() {
    return 3;
  }
}

if (!customElements.get("llm-realtime-audio-card")) {
  customElements.define("llm-realtime-audio-card", LLMRealtimeAudioCard);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: "llm-realtime-audio-card",
  name: "LLM Realtime Audio (Unified + Tools)",
  preview: true,
  description: "Unified audio input and intelligent tool calling via OpenAI Realtime API"
});
