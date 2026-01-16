class LLMCard extends HTMLElement {
  set hass(hass) {
    const oldHass = this._hass;
    this._hass = hass;
    if (!this.content) {
      this.init();
    }
    
    const sensorEntity = this.config?.response_entity || 'sensor.llm_ha_model_response';
    const oldState = oldHass?.states[sensorEntity];
    const newState = hass.states[sensorEntity];
    
    if (!oldState || oldState.state !== newState?.state || 
        oldState.attributes?.full_text !== newState?.attributes?.full_text) {
      this.updateState();
    }
  }

  setConfig(config) {
    this.config = config;
  }

  init() {
    this.attachShadow({ mode: 'open' });
    
    // Initialize state tracking
    this._isLoading = false;
    this._lastFullText = '';
    
    // CSS
    const style = document.createElement('style');
    style.textContent = `
      :host {
        display: block;
      }
      ha-card {
        padding: 16px;
        display: flex;
        flex-direction: column;
        gap: 16px;
      }
      .header {
        font-size: 20px;
        font-weight: 500;
        color: var(--primary-text-color);
      }
      .input-container {
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      textarea {
        width: 100%;
        min-height: 80px;
        resize: vertical;
        padding: 8px;
        border: 1px solid var(--divider-color, #e0e0e0);
        border-radius: 4px;
        background: var(--card-background-color, white);
        color: var(--primary-text-color);
        font-family: inherit;
        box-sizing: border-box;
      }
      .controls {
        display: flex;
        gap: 8px;
        align-items: center;
        justify-content: space-between;
      }
      select {
        padding: 8px;
        border-radius: 4px;
        border: 1px solid var(--divider-color, #e0e0e0);
        background: var(--card-background-color, white);
        color: var(--primary-text-color);
      }
      button {
        background-color: var(--primary-color);
        color: var(--text-primary-color, white);
        border: none;
        padding: 8px 16px;
        border-radius: 4px;
        cursor: pointer;
        font-weight: 500;
        display: flex;
        align-items: center;
        justify-content: center;
        min-width: 80px;
        height: 36px;
        transition: background-color 0.3s;
      }
      button:hover {
        background-color: var(--primary-color-dark, var(--primary-color));
        opacity: 0.9;
      }
      button:disabled {
        background-color: var(--disabled-text-color);
        cursor: not-allowed;
        opacity: 0.6;
      }
      .response-container {
        background-color: var(--card-background-color, white);
        border: 1px solid var(--divider-color, #e0e0e0);
        padding: 16px;
        border-radius: 8px;
        min-height: 60px;
        white-space: pre-wrap;
        color: var(--primary-text-color);
        line-height: 1.5;
        font-size: 14px;
      }
      .response-label {
        font-size: 13px;
        font-weight: 500;
        color: var(--secondary-text-color);
        margin-bottom: 8px;
        display: flex;
        align-items: center;
        gap: 8px;
      }
      /* Spinner */
      .spinner {
        border: 3px solid rgba(255, 255, 255, 0.3);
        border-radius: 50%;
        border-top: 3px solid white;
        width: 16px;
        height: 16px;
        animation: spin 1s linear infinite;
      }
      @keyframes spin {
        0% { transform: rotate(0deg); }
        100% { transform: rotate(360deg); }
      }
    `;

    // HTML Structure
    const card = document.createElement('ha-card');
    this.content = document.createElement('div');
    this.content.innerHTML = `
      <div class="header">LLM Assistant</div>
      
      <div class="input-container">
        <textarea id="prompt-input" placeholder="Ask something..."></textarea>
      </div>

      <div class="controls">
        <select id="model-select">
          <option value="openai">OpenAI (GPT-4o)</option>
          <option value="llama3.3">Llama 3.3</option>
        </select>
        <button id="submit-btn">
          <span class="btn-text">Ask</span>
          <div class="spinner" style="display: none;"></div>
        </button>
      </div>

      <div class="response-container-wrapper">
         <div class="response-label">Response:</div>
         <div id="response-text" class="response-container">No response yet</div>
      </div>
    `;

    card.appendChild(style);
    card.appendChild(this.content);
    this.shadowRoot.appendChild(card);

    // Event Listeners
    this.shadowRoot.getElementById('submit-btn').addEventListener('click', () => this.submit());
    
    // Auto-resize textarea
    const textarea = this.shadowRoot.getElementById('prompt-input');
    textarea.addEventListener('input', function() {
      this.style.height = 'auto';
      this.style.height = (this.scrollHeight) + 'px';
    });
  }

  updateState() {
    if (!this.shadowRoot) return;
    
    const sensorEntity = this.config?.response_entity || 'sensor.llm_ha_model_response';
    const stateObj = this._hass?.states[sensorEntity];
    const responseDiv = this.shadowRoot.getElementById('response-text');
    
    if (!stateObj || !responseDiv) return;
    
    const fullText = stateObj.attributes?.full_text || stateObj.state || '';
    const currentText = responseDiv.innerText || '';
    
    if (fullText && fullText !== this._lastFullText) {
      responseDiv.innerText = fullText;
      this._lastFullText = fullText;
      if (this._isLoading) {
        this.setLoading(false);
      }
    } else if (this._isLoading && fullText && fullText !== currentText) {
      responseDiv.innerText = fullText;
      this._lastFullText = fullText;
      this.setLoading(false);
    }
  }

  setLoading(loading) {
    this._isLoading = loading;
    const btn = this.shadowRoot.getElementById('submit-btn');

    const spinner = this.shadowRoot.querySelector('.spinner');
    const btnText = this.shadowRoot.querySelector('.btn-text');
    
    if (loading) {
      btn.disabled = true;
      spinner.style.display = 'block';
      btnText.style.display = 'none';
    } else {
      btn.disabled = false;
      spinner.style.display = 'none';
      btnText.style.display = 'block';
    }
  }

  async submit() {
    const textInput = this.shadowRoot.getElementById('prompt-input');
    const modelSelect = this.shadowRoot.getElementById('model-select');
    const text = textInput.value;
    const model = modelSelect.value;

    if (!text) return;

    this.setLoading(true);
    this._lastFullText = '';
    
    const sensorEntity = this.config?.response_entity || 'sensor.llm_ha_model_response';
    const initialState = this._hass.states[sensorEntity];
    const initialFullText = initialState?.attributes?.full_text || initialState?.state || '';
    
    const timeoutId = setTimeout(() => {
      if (this._isLoading) {
        console.warn('Response timeout - clearing loading state');
        this.setLoading(false);
      }
    }, 60000);
    
    let checkInterval = null;
    
    try {
      await this._hass.callService('llm_home_assistant', 'chat', {
        text: text,
        model: model
      });
      
      let checkCount = 0;
      const maxChecks = 60;
      checkInterval = setInterval(() => {
        checkCount++;
        const currentState = this._hass.states[sensorEntity];
        const currentFullText = currentState?.attributes?.full_text || currentState?.state || '';
        
        if (currentFullText && currentFullText !== initialFullText) {
          clearTimeout(timeoutId);
          clearInterval(checkInterval);
          this.updateState();
        } else if (checkCount >= maxChecks) {
          clearTimeout(timeoutId);
          clearInterval(checkInterval);
          if (this._isLoading) {
            this.setLoading(false);
          }
        }
      }, 500);
      
    } catch (e) {
      if (checkInterval) clearInterval(checkInterval);
      clearTimeout(timeoutId);
      console.error(e);
      this.setLoading(false);
      alert('Error calling service: ' + e.message);
    }
  }

  getCardSize() {
    return 3;
  }
}

customElements.define('llm-card', LLMCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "llm-card",
  name: "LLM Assistant Card",
  preview: true,
  description: "A custom card for the LLM Home Assistant integration"
});

