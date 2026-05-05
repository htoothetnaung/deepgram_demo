import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import streamlit as st
from dotenv import load_dotenv

from support_functions import FUNCTION_MAP


load_dotenv()

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
MAX_TOOL_ROUNDS = 6


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def http_post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_sec: int = 60,
) -> dict[str, Any]:
    req = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout_sec) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error calling {url}: {exc}") from exc


def http_post_binary(
    url: str,
    body: bytes,
    headers: dict[str, str],
    timeout_sec: int = 60,
) -> bytes:
    req = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=timeout_sec) as resp:
            return resp.read()
    except HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body_text}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error calling {url}: {exc}") from exc


def deepgram_transcribe(
    api_key: str,
    audio_bytes: bytes,
    mime_type: str,
    model: str,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    query = urlencode(
        {
            "model": model,
            "smart_format": "true",
            "punctuate": "true",
            "filler_words": "true",
            "utterances": "true",
        }
    )
    url = f"https://api.deepgram.com/v1/listen?{query}"
    raw = http_post_binary(
        url=url,
        body=audio_bytes,
        headers={
            "Authorization": f"Token {api_key}",
            "Content-Type": mime_type or "audio/wav",
        },
        timeout_sec=60,
    )
    response = json.loads(raw.decode("utf-8"))

    alt = (
        response.get("results", {})
        .get("channels", [{}])[0]
        .get("alternatives", [{}])[0]
    )
    transcript = (alt.get("transcript") or "").strip()
    words = alt.get("words") or []
    return transcript, words, response


def deepgram_tts(
    api_key: str,
    text: str,
    model: str,
) -> bytes:
    query = urlencode({"model": model, "encoding": "mp3"})
    url = f"https://api.deepgram.com/v1/speak?{query}"
    return http_post_binary(
        url=url,
        body=json.dumps({"text": text}).encode("utf-8"),
        headers={
            "Authorization": f"Token {api_key}",
            "Content-Type": "application/json",
        },
        timeout_sec=60,
    )


def llm_chat_completion(
    provider_type: str,
    api_key: str,
    model: str,
    temperature: float,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
    }
    provider = (provider_type or "open_ai").strip().lower()
    if provider in {"google_gemini_openai_compat", "gemini", "google"}:
        return http_post_json(
            url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            payload=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout_sec=60,
        )

    return http_post_json(
        url="https://api.openai.com/v1/chat/completions",
        payload=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout_sec=60,
    )


def format_tool_spec(config: dict[str, Any]) -> list[dict[str, Any]]:
    functions = config["agent"]["think"].get("functions", [])
    return [{"type": "function", "function": fn} for fn in functions]


def normalize_tool_result(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    return json.dumps(raw, ensure_ascii=True)


def execute_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name not in FUNCTION_MAP:
        return {"error": f"Unknown function: {name}"}
    try:
        return FUNCTION_MAP[name](**arguments)
    except Exception as exc:  # pragma: no cover
        return {"error": f"Tool execution failed: {exc}"}


def init_state() -> None:
    if "demo_started" not in st.session_state:
        st.session_state.demo_started = False
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "turn_history" not in st.session_state:
        st.session_state.turn_history = []
    if "transcript_catalog" not in st.session_state:
        st.session_state.transcript_catalog = []
    if "latest_agent_audio" not in st.session_state:
        st.session_state.latest_agent_audio = None
    if "last_audio_sig" not in st.session_state:
        st.session_state.last_audio_sig = None
    if "last_error" not in st.session_state:
        st.session_state.last_error = ""


def add_catalog_row(
    turn: int,
    speaker: str,
    text: str,
    latency_ms: float | None = None,
    detail: str | None = None,
) -> None:
    st.session_state.transcript_catalog.append(
        {
            "turn": turn,
            "speaker": speaker,
            "text": text,
            "latency_ms": None if latency_ms is None else round(latency_ms, 2),
            "detail": detail or "",
        }
    )


def run_agent_response(
    provider_type: str,
    openai_key: str,
    model: str,
    prompt: str,
    tools: list[dict[str, Any]],
    user_text: str,
    temperature: float,
) -> tuple[str, float, float, list[dict[str, Any]]]:
    convo: list[dict[str, Any]] = [
        {"role": "system", "content": prompt},
        *st.session_state.messages,
        {"role": "user", "content": user_text},
    ]
    llm_ms = 0.0
    tool_ms = 0.0
    tool_trace: list[dict[str, Any]] = []
    final_response = ""

    for _ in range(MAX_TOOL_ROUNDS):
        llm_start = time.perf_counter()
        completion = llm_chat_completion(
            provider_type=provider_type,
            api_key=openai_key,
            model=model,
            temperature=temperature,
            messages=convo,
            tools=tools,
        )
        llm_ms += (time.perf_counter() - llm_start) * 1000.0

        choices = completion.get("choices", [])
        if not choices:
            final_response = "(No response choices returned.)"
            convo.append({"role": "assistant", "content": final_response})
            break

        message = choices[0].get("message", {})
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            convo.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": tool_calls,
                }
            )
            for tool_call in tool_calls:
                function_block = tool_call.get("function") or {}
                name = function_block.get("name", "")
                try:
                    args = json.loads(function_block.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_start = time.perf_counter()
                result = execute_tool(name, args)
                tool_ms += (time.perf_counter() - tool_start) * 1000.0
                tool_trace.append({"name": name, "arguments": args, "result": result})
                convo.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", ""),
                        "content": normalize_tool_result(result),
                    }
                )
            continue

        final_response = message.get("content") or "(No response text returned.)"
        convo.append({"role": "assistant", "content": final_response})
        break

    st.session_state.messages.extend(
        [{"role": "user", "content": user_text}, {"role": "assistant", "content": final_response}]
    )
    return final_response, llm_ms, tool_ms, tool_trace


def render_latency_dashboard(in_sidebar: bool = False) -> None:
    st.subheader("Latency & Performance")
    if not st.session_state.turn_history:
        st.caption("Metrics will appear after first voice turn.")
        return

    latest = st.session_state.turn_history[-1]
    if in_sidebar:
        st.metric("Total", f"{latest['total_ms']} ms")
        st.caption(
            f"STT {latest['stt_ms']} ms | LLM {latest['llm_ms']} ms | "
            f"Tools {latest['tool_ms']} ms | TTS {latest['tts_ms']} ms"
        )
    else:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total", f"{latest['total_ms']} ms")
        c2.metric("STT", f"{latest['stt_ms']} ms")
        c3.metric("LLM", f"{latest['llm_ms']} ms")
        c4.metric("Tools", f"{latest['tool_ms']} ms")
        c5.metric("TTS", f"{latest['tts_ms']} ms")

    st.markdown("**Recent Turns (ms)**")
    for t in st.session_state.turn_history[-10:]:
        st.write(
            f"Turn {t['turn']}: total={t['total_ms']} | "
            f"stt={t['stt_ms']} | llm={t['llm_ms']} | "
            f"tools={t['tool_ms']} | tts={t['tts_ms']}"
        )

    with st.expander("Latest Tool Calls", expanded=False):
        latest_trace = latest.get("tool_trace") or []
        if latest_trace:
            st.json(latest_trace)
        else:
            st.write("No tool calls in the latest turn.")


def render_catalog() -> None:
    st.subheader("Real-Time Transcript Catalog")
    if not st.session_state.transcript_catalog:
        st.info("Start the demo to see agent and user transcripts.")
        return
    header_cols = st.columns([1, 1, 6, 2, 3])
    header_cols[0].markdown("**Turn**")
    header_cols[1].markdown("**Speaker**")
    header_cols[2].markdown("**Text**")
    header_cols[3].markdown("**Latency (ms)**")
    header_cols[4].markdown("**Detail**")

    for row in st.session_state.transcript_catalog:
        cols = st.columns([1, 1, 6, 2, 3])
        cols[0].write(row.get("turn", ""))
        cols[1].write(row.get("speaker", ""))
        cols[2].write(row.get("text", ""))
        cols[3].write("" if row.get("latency_ms") is None else row.get("latency_ms"))
        cols[4].write(row.get("detail", ""))


def main() -> None:
    st.set_page_config(page_title="Agentic Voice Performance Lab", layout="wide")
    st.title("Agentic Voice Performance Lab")
    st.caption(
        "Deepgram STT/TTS + tool-calling agent loop with transcript visibility and latency breakdown."
    )

    config = load_config()
    think = config["agent"]["think"]
    speak = config["agent"]["speak"]
    listen = config["agent"]["listen"]
    model = think["provider"]["model"]
    provider_type = think["provider"].get("type", "open_ai")
    temperature_default = float(think["provider"].get("temperature", 0.7))
    prompt = think["prompt"]
    greeting = config["agent"].get("greeting", "Hello, how can I help you today?")
    stt_model = listen["provider"].get("model", "nova-3")
    tts_model = speak["provider"].get("model", "aura-2-thalia-en")
    tools = format_tool_spec(config)

    init_state()

    llm_key = os.environ.get("OPENAI_API_KEY", "")
    if provider_type.strip().lower() in {"google_gemini_openai_compat", "gemini", "google"}:
        llm_key = os.environ.get("GEMINI_API_KEY", "")

    deepgram_key = os.environ.get("DEEPGRAM_API_KEY", "")
    if not llm_key:
        expected = "GEMINI_API_KEY" if provider_type.strip().lower() in {"google_gemini_openai_compat", "gemini", "google"} else "OPENAI_API_KEY"
        st.error(f"Missing {expected} in .env")
        st.stop()
    if not deepgram_key:
        st.error("Missing DEEPGRAM_API_KEY in .env")
        st.stop()

    with st.sidebar:
        st.header("Session")
        temperature = st.slider("LLM Temperature", 0.0, 1.2, temperature_default, 0.1)

        if not st.session_state.demo_started:
            if st.button("Start Demo", type="primary", use_container_width=True):
                st.session_state.demo_started = True
                st.session_state.messages = []
                st.session_state.turn_history = []
                st.session_state.transcript_catalog = []
                st.session_state.latest_agent_audio = None
                st.session_state.last_audio_sig = None
                st.session_state.last_error = ""

                tts_start = time.perf_counter()
                try:
                    greeting_audio = deepgram_tts(deepgram_key, greeting, tts_model)
                    tts_ms = (time.perf_counter() - tts_start) * 1000.0
                    st.session_state.latest_agent_audio = greeting_audio
                    st.session_state.messages.append({"role": "assistant", "content": greeting})
                    add_catalog_row(turn=0, speaker="agent", text=greeting, latency_ms=tts_ms, detail="greeting")
                except Exception as exc:
                    st.error(f"Greeting TTS failed: {exc}")
                st.rerun()
        else:
            if st.button("Stop / Reset Demo", use_container_width=True):
                st.session_state.demo_started = False
                st.session_state.messages = []
                st.session_state.turn_history = []
                st.session_state.transcript_catalog = []
                st.session_state.latest_agent_audio = None
                st.session_state.last_audio_sig = None
                st.session_state.last_error = ""
                st.rerun()

        st.markdown("### Active Models")
        st.code(f"STT: {stt_model}\nLLM Provider: {provider_type}\nLLM Model: {model}\nTTS: {tts_model}")
        render_latency_dashboard(in_sidebar=True)
        if st.session_state.last_error:
            st.error(st.session_state.last_error)

    if not st.session_state.demo_started:
        st.info("Click `Start Demo` to begin voice testing.")
        return

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Voice Input")
        st.write("Record your voice turn, then Streamlit will run STT -> LLM/tools -> TTS.")
        audio_input = st.audio_input("Speak now", label_visibility="collapsed")

        if st.session_state.latest_agent_audio:
            st.subheader("Agent Voice Output")
            st.audio(st.session_state.latest_agent_audio, format="audio/mp3", autoplay=True)

        if audio_input is not None:
            audio_bytes = audio_input.getvalue()
            audio_sig = f"{len(audio_bytes)}:{hash(audio_bytes)}"
            if audio_sig != st.session_state.last_audio_sig:
                st.session_state.last_audio_sig = audio_sig
                turn_number = len(st.session_state.turn_history) + 1
                total_start = time.perf_counter()

                with st.spinner("Processing voice turn..."):
                    try:
                        st.session_state.last_error = ""
                        stt_start = time.perf_counter()
                        transcript, words, _ = deepgram_transcribe(
                            api_key=deepgram_key,
                            audio_bytes=audio_bytes,
                            mime_type=getattr(audio_input, "type", "audio/wav"),
                            model=stt_model,
                        )
                        stt_ms = (time.perf_counter() - stt_start) * 1000.0

                        if not transcript:
                            transcript = "(No transcript detected)"
                        add_catalog_row(
                            turn=turn_number,
                            speaker="user",
                            text=transcript,
                            latency_ms=stt_ms,
                            detail=f"word_count={len(words)}",
                        )

                        llm_response, llm_ms, tool_ms, tool_trace = run_agent_response(
                            provider_type=provider_type,
                            openai_key=llm_key,
                            model=model,
                            prompt=prompt,
                            tools=tools,
                            user_text=transcript,
                            temperature=temperature,
                        )

                        tts_start = time.perf_counter()
                        agent_audio = deepgram_tts(deepgram_key, llm_response, tts_model)
                        tts_ms = (time.perf_counter() - tts_start) * 1000.0
                        st.session_state.latest_agent_audio = agent_audio

                        add_catalog_row(
                            turn=turn_number,
                            speaker="agent",
                            text=llm_response,
                            latency_ms=tts_ms,
                            detail=f"tool_calls={len(tool_trace)}",
                        )

                        total_ms = (time.perf_counter() - total_start) * 1000.0
                        st.session_state.turn_history.append(
                            {
                                "turn": turn_number,
                                "total_ms": round(total_ms, 2),
                                "stt_ms": round(stt_ms, 2),
                                "llm_ms": round(llm_ms, 2),
                                "tool_ms": round(tool_ms, 2),
                                "tts_ms": round(tts_ms, 2),
                                "tool_trace": tool_trace,
                            }
                        )
                        st.rerun()
                    except Exception as exc:
                        error_text = str(exc)
                        st.session_state.last_error = error_text
                        add_catalog_row(
                            turn=turn_number,
                            speaker="system",
                            text="Turn failed",
                            detail=error_text,
                        )
                        st.error(f"Turn failed: {error_text}")

    with right:
        render_catalog()


if __name__ == "__main__":
    main()
