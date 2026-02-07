console.log("LLM Recording Card: Loading...");

const TRANSCRIPTION_ENTITY = 'llm_home_assistant.last_transcription';

class LLMRecordingCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._initialized = false;
    this._processing = false;
    this._processingMinEndTime = 0;
  }

  setConfig(config) {
    this.config = { entities: [TRANSCRIPTION_ENTITY], ...config };
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
    const statusLine = this.shadowRoot.getElementById('statusLine');
    const outputText = this.shadowRoot.getElementById('outputText');
    if (!statusLine || !outputText) return;

    const state = this._hass.states[TRANSCRIPTION_ENTITY];
    const text = state && state.state ? String(state.state) : '';
    const minTimeElapsed = Date.now() >= this._processingMinEndTime;

    if (this._processing) {
      if (text && minTimeElapsed) {
        this._processing = false;
        if (this._processingTickId) clearTimeout(this._processingTickId);
        this._processingTickId = null;
        statusLine.textContent = 'Done';
        statusLine.className = 'status-line';
        outputText.textContent = text;
        outputText.className = 'output-text';
      } else {
        statusLine.textContent = 'Processing…';
        statusLine.className = 'status-line processing';
        outputText.textContent = text || '—';
        outputText.className = 'output-text muted';
      }
    } else {
      statusLine.className = 'status-line';
      statusLine.textContent = 'Ready';
      outputText.textContent = text || '—';
      outputText.className = text ? 'output-text' : 'output-text muted';
    }
  }

  render() {
    const card = document.createElement('ha-card');
    card.header = this.config.title || 'LLM Recording Request';

    const content = document.createElement('div');
    content.style.padding = '16px';

    content.innerHTML = `
      <style>
        .row { margin: 15px 0; }
        label { display: block; margin-bottom: 5px; font-weight: bold; }
        select, button { width: 100%; padding: 10px; margin: 5px 0; font-size: 16px; }
        .toggle-btn { background: #2196F3; color: white; border: none; border-radius: 4px; cursor: pointer; }
        .toggle-btn.recording { background: #f44336; }
        .status-box {
          display: block;
          margin-top: 16px;
          min-height: 88px;
          padding: 12px 14px;
          background: #e8e8e8;
          border: 1px solid #b0b0b0;
          border-radius: 6px;
          box-sizing: border-box;
        }
        .status-line {
          font-size: 14px;
          font-weight: 600;
          margin-bottom: 8px;
          color: #333;
        }
        .status-line.processing {
          color: #1565c0;
          font-style: italic;
        }
        .output-text {
          font-size: 14px;
          line-height: 1.45;
          white-space: pre-wrap;
          word-break: break-word;
          color: #111;
        }
        .output-text.muted {
          color: #666;
        }
      </style>

      <div class="row">
        <button id="toggleBtn" class="toggle-btn">Start Recording</button>
      </div>

      <div class="row">
        <label for="modelSelect">LLM Model:</label>
        <select id="modelSelect">
          <option value="openai">OpenAI (GPT-4o)</option>
          <option value="llama3.3">Llama 3.3</option>
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

    this.shadowRoot.getElementById('toggleBtn').addEventListener('click', () => this.toggleRecording());
    this.shadowRoot.getElementById('modelSelect').addEventListener('change', (e) => this.changeModel(e.target.value));
    this._updateFromHass();
  }

  toggleRecording() {
    const btn = this.shadowRoot.getElementById('toggleBtn');
    const isRecording = btn.classList.contains('recording');
    const service = isRecording ? 'stop_recording' : 'start_recording';

    this._hass.callService('llm_home_assistant', service, {}).then(() => {
      if (isRecording) {
        btn.classList.remove('recording');
        btn.textContent = 'Start Recording';
        this._processing = true;
        this._processingMinEndTime = Date.now() + 1800;
        this._updateFromHass();
        this._processingTick();
      } else {
        btn.classList.add('recording');
        btn.textContent = 'Stop Recording';
        this._processing = false;
        this._updateFromHass();
      }
    }).catch(err => {
      console.error("Error calling recording service:", err);
      this._processing = false;
      this._updateFromHass();
    });
  }

  _processingTick() {
    if (!this._processing) return;
    if (Date.now() < this._processingMinEndTime) {
      this._processingTickId = setTimeout(() => this._processingTick(), 200);
      return;
    }
    this._processingTickId = null;
    this._updateFromHass();
  }

  changeModel(model) {
    const entity = this.config.select_entity || 'select.llm_model_select';
    this._hass.callService('select', 'select_option', {
      entity_id: entity,
      option: model
    }).then(() => {}).catch(err => {
      console.error("Error changing model:", err);
    });
  }

  getCardSize() {
    return 3;
  }
}

if (!customElements.get('llm-my-recording-card')) {
  customElements.define('llm-my-recording-card', LLMRecordingCard);
}
console.log("LLM Recording Card ready!");
