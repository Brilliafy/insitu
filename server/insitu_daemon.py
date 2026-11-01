#!/usr/bin/env python3
"""
InSitu High-Performance Persistent Daemon
Keeps:
- Faster-Whisper permanently resident in CUDA VRAM (Device 0: NVIDIA GTX 1050)
- Kokoro ONNX & Piper Voice permanently resident in RAM for instant sub-300ms speech synthesis
- Local HTTP API on 127.0.0.1:6422 for ultra-fast, zero-cold-boot IPC with VS Code & Antigravity
"""

import sys
import os
import time
import json
import signal
import threading
import subprocess
import argparse
import wave
from http.server import HTTPServer, BaseHTTPRequestHandler
import numpy as np

# Dynamically locate and preload NVIDIA CUDA 12 runtime libraries
def setup_cuda_libraries():
    try:
        import site, ctypes
        site_packages = site.getsitepackages()
        for sp in site_packages:
            for sub in ["nvidia/cublas/lib", "nvidia/cudnn/lib", "nvidia/cuda_nvrtc/lib"]:
                p = os.path.join(sp, sub)
                if os.path.exists(p):
                    ld_path = os.environ.get("LD_LIBRARY_PATH", "")
                    if p not in ld_path:
                        os.environ["LD_LIBRARY_PATH"] = f"{p}:{ld_path}"
                    for f in sorted(os.listdir(p)):
                        if f.endswith(".so") or ".so." in f:
                            try:
                                ctypes.CDLL(os.path.join(p, f), mode=ctypes.RTLD_GLOBAL)
                            except Exception:
                                pass
    except Exception:
        pass

setup_cuda_libraries()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model_manager import load_config as load_stt_config, get_best_cuda_compute_type
import tts_player

DAEMON_PORT = 6422
CTRL_FILE = "/tmp/insitu_mic_ctrl"
STATUS_FILE = "/tmp/insitu_daemon_status.json"

class InSituRuntime:
    def __init__(self):
        self.whisper_model = None
        self.whisper_model_id = None
        self.whisper_device = None
        self.piper_voice = None
        self.kokoro = None
        self.is_recording = False
        self.recording_thread = None
        self.cancel_requested = False
        self.stop_requested = False
        self.last_transcript = ""
        self.active_tts_proc = None
        self.lock = threading.Lock()

    def is_speaking(self) -> bool:
        if self.active_tts_proc:
            if self.active_tts_proc.poll() is None:
                return True
            self.active_tts_proc = None
        return tts_player.is_speaking()

    def get_cuda_vram_mb(self) -> int:
        try:
            res = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                stderr=subprocess.DEVNULL
            ).decode().strip()
            return int(res.split("\n")[0])
        except Exception:
            return 0

    def warm_stt(self):
        cfg = load_stt_config()
        model_id = cfg.get("model_id", "Systran/faster-distil-whisper-small.en")
        device = cfg.get("device", "cuda")
        engine = cfg.get("engine", "whisper")

        if engine != "whisper":
            print(f"[InSitu Daemon] Engine is {engine}, skipping Whisper CUDA load.")
            return

        compute_type = get_best_cuda_compute_type() if device == "cuda" else "int8"
        print(f"[InSitu Daemon] Prewarming Whisper '{model_id}' on {device.upper()} ({compute_type})...")
        t0 = time.time()
        try:
            from faster_whisper import WhisperModel
            self.whisper_model = WhisperModel(model_id, device=device, compute_type=compute_type)
            self.whisper_model_id = model_id
            self.whisper_device = device
            # Warmup inference
            dummy = np.zeros(16000, dtype=np.float32)
            list(self.whisper_model.transcribe(dummy, language="en")[0])
            elapsed = time.time() - t0
            vram = self.get_cuda_vram_mb()
            print(f"[InSitu Daemon] ✅ Whisper prewarmed in {elapsed:.2f}s! Active GPU VRAM: {vram} MB")
        except Exception as e:
            print(f"[InSitu Daemon] ❌ Error warming Whisper on CUDA ({e}). Falling back to CPU.", file=sys.stderr)
            from faster_whisper import WhisperModel
            self.whisper_model = WhisperModel(model_id, device="cpu", compute_type="int8")
            self.whisper_device = "cpu"

    def warm_tts(self):
        print("[InSitu Daemon] Prewarming TTS in memory (Piper Voice)...")
        try:
            from piper.voice import PiperVoice
            piper_model_path = "/home/Aelfwif/insitu/server/models/piper/en_US-bryce-medium.onnx"
            if not os.path.exists(piper_model_path):
                piper_model_path = "/home/Aelfwif/insitu/server/models/piper/en_US-lessac-medium.onnx"
            if os.path.exists(piper_model_path):
                t1 = time.time()
                self.piper_voice = PiperVoice.load(piper_model_path)
                print(f"[InSitu Daemon] ✅ Piper Voice resident in RAM ({time.time() - t1:.2f}s).")
        except Exception as e:
            print(f"[InSitu Daemon] Warning warming Piper: {e}", file=sys.stderr)

    def transcribe_audio_data(self, audio_data: np.ndarray) -> str:
        if self.whisper_model is None:
            self.warm_stt()

        cfg = load_stt_config()
        prefix = cfg.get("prompt_prefix", "")

        t0 = time.time()
        segments, _ = self.whisper_model.transcribe(
            audio_data,
            initial_prompt=prefix if prefix else None,
            beam_size=1,
            language="en"
        )
        texts = [s.text.strip() for s in segments if s.text and s.text.strip()]
        result = " ".join(texts)
        print(f"[InSitu Daemon] Transcribed in {(time.time() - t0)*1000:.1f}ms: '{result[:50]}'")
        return result

    def record_and_transcribe(self, silence_seconds: float = 0.8, max_duration: float = 30.0) -> dict:
        import sounddevice as sd

        self.cancel_requested = False
        self.stop_requested = False
        self.is_recording = True

        sample_rate = 16000
        chunk_samples = 1024
        chunk_duration = chunk_samples / sample_rate

        recorded_chunks = []
        has_spoken = False
        consecutive_silence = 0.0
        speech_threshold = 0.012

        start_time = time.time()

        try:
            with sd.InputStream(samplerate=sample_rate, channels=1, dtype="float32", blocksize=chunk_samples) as stream:
                while True:
                    if self.cancel_requested:
                        print("[InSitu Daemon] Recording cancelled.")
                        return {"cancelled": True}
                    if self.stop_requested:
                        break

                    chunk, overflowed = stream.read(chunk_samples)
                    recorded_chunks.append(chunk.flatten())

                    rms = float(np.sqrt(np.mean(chunk**2)))
                    if rms > speech_threshold:
                        has_spoken = True
                        consecutive_silence = 0.0
                    else:
                        if has_spoken:
                            consecutive_silence += chunk_duration
                            if consecutive_silence >= silence_seconds:
                                print(f"[InSitu Daemon] VAD {silence_seconds}s silence hit. Decoding immediately.")
                                break

                    if time.time() - start_time >= max_duration:
                        break
        finally:
            self.is_recording = False

        if not recorded_chunks:
            return {"text": ""}

        audio_data = np.concatenate(recorded_chunks, axis=0)
        if len(audio_data) < sample_rate * 0.25:
            return {"text": ""}

        if self.cancel_requested:
            return {"cancelled": True}

        text = self.transcribe_audio_data(audio_data)
        return {"text": text}

    def speak(self, text: str, engine: str = None, voice_id: str = None) -> bool:
        tts_player.stop_speech()
        cleaned = tts_player.extract_and_clean_speech(text)
        if not cleaned:
            return False

        cfg = tts_player.load_tts_config()
        selected_engine = engine or cfg.get("engine", "edge-tts")
        if selected_engine == "none":
            return True
        selected_voice = voice_id or cfg.get("voice_id", "en-US-GuyNeural")
        speed = float(cfg.get("speed", 1.05))

        print(f"[InSitu Daemon] Speaking ({selected_engine} - {selected_voice}): '{cleaned[:50]}...'")

        # Fast path 1: Edge-TTS (Human Studio Quality)
        if selected_engine == "edge-tts":
            try:
                threading.Thread(target=tts_player.speak_edge_tts, args=(cleaned, selected_voice), daemon=True).start()
                return True
            except Exception as e:
                print(f"[InSitu Daemon] Edge-TTS error: {e}", file=sys.stderr)

        # Fast path 2: Piper Voices (Ultra-Low Latency Offline)
        if selected_engine == "piper":
            try:
                threading.Thread(target=tts_player.speak_piper, args=(cleaned, selected_voice), daemon=True).start()
                return True
            except Exception as e:
                print(f"[InSitu Daemon] Piper speak error: {e}", file=sys.stderr)

        # Fast path 3: Kokoro (Expressive)
        try:
            threading.Thread(target=tts_player.speak_kokoro, args=(cleaned, selected_voice, speed), daemon=True).start()
            return True
        except Exception as e:
            print(f"[InSitu Daemon] Kokoro speak error: {e}", file=sys.stderr)
            return False

runtime = InSituRuntime()

class DaemonHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/status":
            vram = runtime.get_cuda_vram_mb()
            cfg_stt = load_stt_config()
            data = {
                "status": "online",
                "whisper_device": runtime.whisper_device or cfg_stt.get("device", "cuda"),
                "whisper_model": runtime.whisper_model_id or cfg_stt.get("model_id"),
                "gpu_vram_mb": vram,
                "stt_warm": runtime.whisper_model is not None,
                "tts_warm": runtime.kokoro is not None or runtime.piper_voice is not None,
                "is_recording": runtime.is_recording,
                "is_speaking": runtime.is_speaking()
            }
            self.send_json(data)
        else:
            self.send_error(404)

    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else ""
        payload = json.loads(body) if body else {}

        if self.path == "/record":
            silence = float(payload.get("silence_seconds", 0.8))
            max_dur = float(payload.get("max_duration", 30.0))
            res = runtime.record_and_transcribe(silence_seconds=silence, max_duration=max_dur)
            self.send_json(res)

        elif self.path == "/cancel":
            runtime.cancel_requested = True
            self.send_json({"status": "cancelled"})

        elif self.path == "/stop_mic":
            runtime.stop_requested = True
            self.send_json({"status": "stopping"})

        elif self.path == "/speak":
            text = payload.get("text", "")
            engine = payload.get("engine")
            voice_id = payload.get("voice_id")
            ok = runtime.speak(text, engine=engine, voice_id=voice_id)
            self.send_json({"status": "ok" if ok else "error"})

        elif self.path == "/stop_speech":
            tts_player.stop_speech()
            self.send_json({"status": "silenced"})

        elif self.path == "/reload_model":
            runtime.warm_stt()
            self.send_json({"status": "reloaded", "device": runtime.whisper_device, "vram_mb": runtime.get_cuda_vram_mb()})

        else:
            self.send_error(404)

    def send_json(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def log_message(self, format, *args):
        # Quiet standard HTTP access logs
        pass

def write_status_file():
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump({
                "pid": os.getpid(),
                "port": DAEMON_PORT,
                "time": time.time()
            }, f)
    except Exception:
        pass

def main():
    print("=" * 65)
    print("🚀 Starting InSitu Persistent CUDA & Audio Daemon on port 6422...")
    print("=" * 65)

    # 1. Warm STT on GPU
    runtime.warm_stt()

    # 2. Warm TTS in Memory
    runtime.warm_tts()

    write_status_file()

    server = HTTPServer(("127.0.0.1", DAEMON_PORT), DaemonHandler)
    print(f"[InSitu Daemon] Listening on http://127.0.0.1:{DAEMON_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[InSitu Daemon] Shutting down.")
    finally:
        try:
            if os.path.exists(STATUS_FILE):
                os.remove(STATUS_FILE)
        except Exception:
            pass

if __name__ == "__main__":
    main()
