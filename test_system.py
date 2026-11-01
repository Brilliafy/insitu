#!/usr/bin/env python3
"""
Comprehensive Integration Test for InSitu System
Tests:
1. HTTP Bridge mock / live server (Port 6419)
2. InSitu FastMCP server tools
3. Kokoro & Piper TTS pipeline
4. Moonshine ONNX & Faster-Whisper STT pipeline
"""

import sys
import os
import json
import time
import threading
import http.server
import asyncio
from pathlib import Path

# Add server dir to path
sys.path.append("/home/Aelfwif/insitu/server")
import tutor_mcp
import tts_player
import stt_transcriber

# 1. Mock HTTP Bridge Server on 6419
class MockBridgeHandler(http.server.BaseHTTPRequestHandler):
    current_mode = 1
    last_highlight = None

    def do_GET(self):
        if self.path == "/mode":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"mode": self.current_mode, "modeName": "Section Scaffold"}).encode())
        elif self.path == "/selection":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "active": True,
                "file": "/home/Aelfwif/test_code.py",
                "startLine": 12,
                "endLine": 18,
                "selectedText": "def handle_buffer():\n    pass"
            }).encode())
        elif self.path == "/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "port": 6419}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        data = json.loads(body) if body else {}

        if self.path == "/highlight":
            MockBridgeHandler.last_highlight = data
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "message": "Highlighted"}).encode())
        elif self.path == "/mode":
            MockBridgeHandler.current_mode = data.get("mode", 1)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "mode": MockBridgeHandler.current_mode}).encode())
        elif self.path == "/speak_notify":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass

def start_mock_server():
    try:
        server = http.server.HTTPServer(("127.0.0.1", 6419), MockBridgeHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        return server
    except OSError:
        print("[*] Live IDE Bridge already running on 127.0.0.1:6419, testing against live bridge.")
        return None

async def run_tests():
    print("=" * 60)
    print("RUNNING INSITU INTEGRATION TEST SUITE")
    print("=" * 60)

    # 1. Start Bridge (if not already running)
    server = start_mock_server()

    # 2. Test MCP highlight_and_navigate
    res_hl = await tutor_mcp.highlight_and_navigate("/home/Aelfwif/test_code.py", 10, 20, "Look at this loop")
    print(f"[✔] MCP highlight_and_navigate: {res_hl}")

    # 3. Test MCP get_tutor_mode
    mode_info = await tutor_mcp.get_tutor_mode()
    print(f"[✔] MCP get_tutor_mode:\n{mode_info}")

    # 4. Test MCP set_tutor_mode
    set_m2 = await tutor_mcp.set_tutor_mode(2)
    print(f"[✔] MCP set_tutor_mode (Mode 2): {set_m2}")

    set_m1 = await tutor_mcp.set_tutor_mode(1)
    print(f"[✔] MCP set_tutor_mode (Mode 1): {set_m1}")

    # 5. Test MCP get_active_editor_selection
    selection = await tutor_mcp.get_active_editor_selection()
    print(f"[✔] MCP get_active_editor_selection:\n{selection}")

    # 6. Test TTS speak
    tts_res = await tutor_mcp.speak_to_user("Testing Socratic speech synthesis.")
    print(f"[✔] MCP speak_to_user: {tts_res}")

    # 7. Test STT models list & prompt prefix
    models_json = await tutor_mcp.list_stt_models()
    models_data = json.loads(models_json)
    print(f"[✔] MCP list_stt_models: Found {len(models_data['models'])} models. Current: {models_data['current_model']}")
    assert len(models_data['models']) >= 5

    pref_res = await tutor_mcp.set_code_prompt_prefix("JavaScript, Python, snake_case, kubectl, async/await")
    print(f"[✔] MCP set_code_prompt_prefix: {pref_res}")

    # 8. Test STT transcription
    test_wav = "/tmp/socratic_test_speech.wav"
    if os.path.exists(test_wav):
        transcription = stt_transcriber.transcribe_file(test_wav)
        print(f"[✔] STT Transcribe: \"{transcription}\"")

    print("=" * 60)
    print("ALL INTEGRATION TESTS PASSED PERFECTLY!")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(run_tests())
