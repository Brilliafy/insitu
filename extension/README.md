# InSitu: In-Editor Voice & Socratic AI Mentor

<div align="center">
  <img src="icon.png" width="96" height="96" alt="InSitu Pixel Philosopher Logo" />
</div>

In-Window Topmost HUD Overlay with Low-Latency STT/TTS Voice Dictation, Visual In-Editor Navigation, and Dual-Channel AI Mentorship custom-built for **Google Antigravity** and VS Code.

## Features

- **Customized for Google Antigravity:** Direct native IPC integration via `antigravity.addContext` and `antigravity.triggerSend`, native agent panel auto-focus, and integrated FastMCP server.
- **In-Window Floating HUD Capsule (`Ctrl+Alt+T`)**: Minimalist, frameless HUD overlay for hands-free pair-programming.
- **Voice Dictation (`Ctrl+Alt+M`)**: Low-latency local transcription with coding vocabulary bias (`snake_case`, `kubectl`, `async/await`).
- **Dual-Channel Voice Mentorship**: Full technical explanations in chat paired with punchy 1–2 sentence human spoken audio companions via `speak_to_user`.
- **Mode 1 In-File Code Replacement**: Directly replaces and edits code in the active file buffer instead of just dumping text in chat.
- **AI File Modifications Toggle**: Instantly switch between **ENABLED (Direct Code Replacement)** and **DISABLED (Read-Only / Explanations Only)** when asking questions or exploring caveats.
- **Multi-Engine TTS & Silent Mode**:
  - **Edge Neural TTS:** 26 studio-quality human voices across American, British, Australian, Canadian, Irish, and Indian English accents.
  - **Piper ONNX:** 6 instant offline voices (<150ms latency).
  - **Kokoro-82M ONNX:** 54 expressive voices across US, UK, Japanese, and Mandarin.
  - **None (Silent Mode):** Mutes all voice synthesis; MCP calls succeed silently without playback.
- **CUDA Hardware Acceleration (`Ctrl+Alt+G`)**: Instant one-click toggling between CPU INT8 and GPU CUDA FP16 with prewarmed VRAM caching (~350 MB on GTX 1050+).
- **3 Socratic Pedagogical Modes**:
  1. Section Scaffold (Feature flow & direct file replacement)
  2. Line-by-Line Step (Pedagogical micro-steps)
  3. Refactor & Doubt (Interactive code inspection & trade-off analysis)
- **Disk Space Reclaim**: Inspect on-disk model cache sizes and purge weights with 1 click.
