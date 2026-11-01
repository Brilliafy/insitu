#!/usr/bin/env python3
"""
Unified Socratic Tutor MCP Server for OpenCode and Antigravity (agy / IDE).
Provides tools for:
- Viewport control & Visual code range highlighting
- Low-latency spoken audio (Piper TTS -> PipeWire/ALSA)
- 3 Socratic teaching modes:
    1) Section Scaffold
    2) Line-by-Line Step
    3) Refactor / Doubt Exploration
- Editor selection inspection
- Low-latency local voice transcription (Moonshine ONNX)
"""

import sys
import os
import subprocess
import urllib.request
import json
import shutil
from pathlib import Path
from mcp.server.fastmcp import FastMCP

import tts_player
import threading

# Pre-warm fast Piper TTS model in background
threading.Thread(target=tts_player.load_tts_config, daemon=True).start()
SERVER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SERVER_DIR))
PIPER_MODEL = SERVER_DIR / "models" / "piper" / "en_US-lessac-medium.onnx"
PIPER_BIN = "/home/Aelfwif/anaconda3/envs/socratic-tutor/bin/piper"
TTS_PLAYER = SERVER_DIR / "tts_player.py"
STT_TRANSCRIBER = SERVER_DIR / "stt_transcriber.py"
PYTHON_BIN = "/home/Aelfwif/anaconda3/envs/socratic-tutor/bin/python"
BRIDGE_URL = "http://127.0.0.1:6419"

mcp = FastMCP("InteractiveTutor")

def query_bridge(endpoint: str, data: dict = None, method: str = "GET") -> dict:
    url = f"{BRIDGE_URL}{endpoint}"
    try:
        if data is not None:
            payload = json.dumps(data).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method=method)
        else:
            req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=2.0) as res:
            return json.loads(res.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e), "offline": True}

@mcp.tool()
async def highlight_and_navigate(file_path: str, start_line: int, end_line: int, message: str = "") -> str:
    """Focuses the editor on a specific file and highlights line ranges for the user in Antigravity IDE / VS Code."""
    res = query_bridge("/highlight", {
        "file": os.path.abspath(file_path),
        "startLine": int(start_line),
        "endLine": int(end_line),
        "message": message
    }, method="POST")

    if res.get("status") == "ok":
        return f"Editor viewport successfully scrolled to {os.path.basename(file_path)}:{start_line}-{end_line} and highlighted."
    elif res.get("offline"):
        return f"Note: IDE companion bridge at 127.0.0.1:6419 is not connected. Target would be {file_path}:{start_line}-{end_line}."
    else:
        return f"Failed to highlight in editor: {res.get('error', 'Unknown error')}"

@mcp.tool()
async def clear_highlight() -> str:
    """Clears any active Socratic highlight decorations in the user's IDE editor."""
    res = query_bridge("/clear_highlight", {}, method="POST")
    if res.get("status") == "ok":
        return "Editor highlight cleared."
    elif res.get("offline"):
        return "IDE companion bridge is not connected."
    return f"Failed to clear highlight: {res.get('error', 'Unknown')}"

@mcp.tool()
async def speak_to_user(message: str) -> str:
    """Speaks a concise Socratic question, hint, or architectural consideration to the user out loud using the active TTS engine (Edge Neural / Piper / Kokoro).
    CRITICAL SPOKEN CONSTRAINTS:
    - Keep spoken audio SHORT (1-2 sentences, 15-25 words maximum).
    - Sound like an experienced human peer pair-programming with the user: natural, conversational, clear, and direct.
    - Adapt tone to the user's expertise level (junior vs senior).
    - Never read aloud raw syntax or multiline code blocks.
    - Directly answer the core question or pose a focused guiding question."""
    try:
        cfg = tts_player.load_tts_config()
        engine = cfg.get("engine", "edge-tts")
        if engine == "none":
            return "TTS is disabled (None). Audio is silenced."

        voice_id = cfg.get("voice_id", "en-US-GuyNeural")
        # 1. Fast path: Forward to InSitu warm daemon (sub-50ms dispatch)
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:6422/speak",
                data=json.dumps({
                    "text": message,
                    "engine": engine,
                    "voice_id": voice_id
                }).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=1.5):
                return f"Spoken audio playing aloud via InSitu Daemon ({engine} - {voice_id})."
        except Exception:
            pass

        # 2. Fallback: Launch directly in background thread with warm in-memory model
        threading.Thread(target=tts_player.speak, args=(message,), kwargs={"force": True, "blocking": True}, daemon=True).start()
        return "Spoken message dispatched via InSitu TTS fallback."
    except Exception as e:
        return f"Failed to speak message: {str(e)}"

@mcp.tool()
async def stop_speech() -> str:
    """Instantly silences and stops any currently active spoken audio."""
    try:
        try:
            req = urllib.request.Request("http://127.0.0.1:6422/stop_speech", data=b"{}", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=0.5):
                pass
        except Exception:
            pass
        tts_player.stop_speech()
        return "Spoken audio stopped and silenced."
    except Exception as e:
        return f"Failed to stop speech: {str(e)}"

@mcp.tool()
async def get_tutor_mode() -> str:
    """Retrieves the currently selected Socratic mode (1: Section Scaffold, 2: Line Step, 3: Refactor/Doubt) and returns strict pedagogical constraints."""
    res = query_bridge("/mode")
    mode = res.get("mode", 1)
    mode_name = res.get("modeName", "Section Scaffold")

    guidance = {
        1: (
            "MODE 1: SECTION SCAFFOLD (Feature Flow)\n"
            "- Focus editor on relevant file and section using `highlight_and_navigate`.\n"
            "- Ask a concise Socratic question on how to proceed, what edge cases exist, or architectural design.\n"
            "- Call `speak_to_user` with your question or architectural insight first.\n"
            "- When replacing or scaffolding code, DIRECTLY EDIT and REPLACE the highlighted section in the active file using your file editing tools (do NOT implement the whole file, only the immediate section).\n"
            "- Highlight the newly written code and give brief considerations on what's next in chat."
        ),
        2: (
            "MODE 2: LINE-BY-LINE STEP (Pedagogical Micro-Steps)\n"
            "- Focus editor and highlight the exact line position using `highlight_and_navigate`.\n"
            "- Write ONLY ONE logical line or statement.\n"
            "- Ask the user about the next statement, variable type, syntax, or invariant.\n"
            "- Call `speak_to_user` and STOP execution until they answer."
        ),
        3: (
            "MODE 3: REFACTOR & DOUBT EXPLORATION (Deep Dive)\n"
            "- The user selects code or raises a doubt/question.\n"
            "- Use `get_active_editor_selection` to inspect the code they selected.\n"
            "- Highlight the exact lines using `highlight_and_navigate`.\n"
            "- Explain the mechanism, trade-offs, potential concurrency/memory bugs or code smells.\n"
            "- Call `speak_to_user` and prompt them with a targeted refactoring question."
        )
    }

    return f"Active Mode: Mode {mode} ({mode_name})\n\nRules to follow:\n{guidance.get(mode, guidance[1])}"

@mcp.tool()
async def set_tutor_mode(mode: int) -> str:
    """Sets the active Socratic tutor mode (1 for Section Scaffold, 2 for Line Step, 3 for Refactor/Doubt)."""
    if mode not in (1, 2, 3):
        return "Invalid mode. Choose 1 (Section Scaffold), 2 (Line Step), or 3 (Refactor/Doubt)."
    res = query_bridge("/mode", {"mode": mode}, method="POST")
    return f"Tutor mode updated to Mode {mode} in IDE HUD and Server."

@mcp.tool()
async def get_active_editor_selection() -> str:
    """Inspects the active editor document, selection range, and selected text in Antigravity IDE / VS Code."""
    res = query_bridge("/selection")
    if res.get("active"):
        return json.dumps({
            "file": res.get("file"),
            "start_line": res.get("startLine"),
            "end_line": res.get("endLine"),
            "selected_text": res.get("selectedText")
        }, indent=2)
    elif res.get("offline"):
        return "IDE bridge is currently offline (editor not open)."
    else:
        return "No text editor or selection currently active in IDE."

@mcp.tool()
async def listen_for_user_voice(duration_seconds: float = 4.0) -> str:
    """Records audio from the user's microphone for the specified duration and transcribes it locally using Moonshine ONNX."""
    try:
        proc = subprocess.run(
            [PYTHON_BIN, str(STT_TRANSCRIBER), "--mic", str(duration_seconds)],
            capture_output=True,
            text=True,
            timeout=duration_seconds + 5
        )
        data = json.loads(proc.stdout.strip())
        transcription = data.get("text", "")
        if transcription:
            return f"Transcribed user voice: \"{transcription}\""
        return "No speech detected from microphone."
    except Exception as e:
        return f"Error capturing or transcribing voice: {str(e)}"

@mcp.tool()
async def list_stt_models() -> str:
    """Lists available Speech-to-Text models across tiers (Ultra-lightweight to high-end), active model, and cache status."""
    from model_manager import list_models
    data = list_models()
    return json.dumps(data, indent=2)

@mcp.tool()
async def set_stt_model(model_id: str) -> str:
    """Changes the active STT model (e.g. 'moonshine/tiny', 'Systran/faster-whisper-tiny.en', 'Systran/faster-distil-whisper-small.en', or custom Hugging Face repo ID). Downloads automatically if needed."""
    from model_manager import set_active_model
    res = set_active_model(model_id, auto_download=True)
    if res.get("status") == "ok":
        return f"STT model successfully updated to: {model_id} (Engine: {res.get('engine')})"
    return f"Failed to switch STT model: {res.get('error', 'Unknown error')}"

@mcp.tool()
async def set_code_prompt_prefix(prefix: str) -> str:
    """Configures the programming vocabulary prompt prefix (e.g. 'snake_case, kubectl, async/await') to bias speech recognition toward coding syntax."""
    from model_manager import load_config, save_config
    cfg = load_config()
    cfg["prompt_prefix"] = prefix
    save_config(cfg)
    return f"Coding vocabulary prompt prefix updated to:\n{prefix}"

@mcp.tool()
async def get_code_edits_mode() -> str:
    """Checks whether AI file modifications are currently ENABLED (Direct File Replacement) or DISABLED (Read-Only / Explanations Only)."""
    res = query_bridge("/code_edits")
    allow = res.get("allowCodeEdits", True)
    if allow:
        return "AI Code Modifications: ENABLED. The mentor should directly edit and replace sections in the active file using editing tools."
    else:
        return "AI Code Modifications: DISABLED (Read-Only). The mentor MUST NOT edit or write to any files. Provide explanations and caveat analysis in chat only."

@mcp.tool()
async def set_code_edits_mode(allow: bool) -> str:
    """Toggles whether the AI mentor directly modifies files (True) or operates in read-only explanation mode (False)."""
    res = query_bridge("/code_edits", {"allow": allow}, method="POST")
    state_str = "ENABLED" if res.get("allowCodeEdits", allow) else "DISABLED (Read-Only)"
    return f"AI Code Modifications updated to: {state_str} in InSitu IDE HUD."

if __name__ == "__main__":
    mcp.run()
