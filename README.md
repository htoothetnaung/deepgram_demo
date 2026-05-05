## Deepgram Testing Lab

Twilio-free local test harness for your customer-support agent.

### Streamlit UI (recommended for Myanmar testing)

This UI lets you:
- run voice turns without calling a phone number,
- click `Start Demo` and hear the agent greeting immediately,
- speak your turn via microphone and get agent voice response,
- inspect transcript catalog for both customer and agent turns,
- exercise tool-calling (`get_customer_data`, `check_order`, `refund`, `escalate`),
- monitor latency per turn (STT, LLM, tools, TTS, total).

### Run

From `deepgram_testing/`:

```bash
uv sync
uv run streamlit run streamlit_app.py
```

If you are not using `uv`:

```bash
pip install -e .
streamlit run streamlit_app.py
```

### Required env vars

In `.env`:

```bash
OPENAI_API_KEY=your_openai_key
GEMINI_API_KEY=your_gemini_key
DEEPGRAM_API_KEY=your_deepgram_key
```

Notes:
- `GEMINI_API_KEY` is required when `config.json` uses `google_gemini_openai_compat`.
- `OPENAI_API_KEY` is required only when `config.json` uses `open_ai`.
- `DEEPGRAM_API_KEY` is required for the Twilio websocket bridge in `main.py`.

### Voice demo flow

1. Launch Streamlit and open the page.
2. Click `Start Demo`.
3. Wait for the greeting audio.
4. Use the microphone input to record each user turn.
5. Review:
   - Latency charts and per-stage metrics
   - Transcript catalog (customer vs agent text)
   - Tool call traces for each turn
