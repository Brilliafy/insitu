#!/usr/bin/env python3
"""
InSitu STT Transcriber with VAD, Background Prewarming, Dynamic Model Loading & Coding Prompt Bias
Features:
- Real-time audio streaming with configurable silence autostop (default 1.2s for instant response)
- Background model prewarming during recording for sub-second transcription turnaround
- True GPU CUDA acceleration (Pascal int8_float32 / Ampere float16) with libcublas/cudnn preload
- Control commands: STOP (manually finish) & CANCEL (abort immediately without sending prompt)
- Coding vocabulary prompt bias for snake_case, kubectl, async/await, camelCase, etc.
"""

import sys
import os
import time
import json
import tempfile
import subprocess
import argparse
import threading
import numpy as np

# Dynamically preload CUDA runtime libraries from nvidia pip packages if available
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

# Import model manager
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model_manager import load_config

CTRL_FILE = "/tmp/insitu_mic_ctrl"
_MODEL_CACHE = {}

def get_best_cuda_compute_type() -> str:
    """Returns the best supported CTranslate2 compute type for current CUDA GPU."""
    try:
        import ctranslate2
        supported = ctranslate2.get_supported_compute_types("cuda")
        # Preference: float16 (modern RTX), int8_float32 (Pascal GTX 1050/1650), int8, float32
        for pref in ["float16", "int8_float32", "int8", "float32"]:
            if pref in supported:
                return pref
    except Exception:
        pass
    return "int8_float32"

def get_whisper_model(model_id: str, device: str = "cpu", compute_type: str = "int8"):
    global _MODEL_CACHE
    key = (model_id, device)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]

    from faster_whisper import WhisperModel
    if device == "cuda":
        best_compute = get_best_cuda_compute_type()
        try:
            print(f"[InSitu STT] Initializing Whisper model on CUDA (compute_type={best_compute})...", file=sys.stderr)
            model = WhisperModel(model_id, device="cuda", compute_type=best_compute)
            _MODEL_CACHE[key] = model
            return model
        except Exception as e:
            print(f"[InSitu STT] Warning: CUDA error ({e}). Falling back to CPU.", file=sys.stderr)
            model = WhisperModel(model_id, device="cpu", compute_type="int8")
            _MODEL_CACHE[(model_id, "cpu")] = model
            return model
    else:
        model = WhisperModel(model_id, device="cpu", compute_type="int8")
        _MODEL_CACHE[key] = model
        return model

def preload_active_model_async():
    """Warms the active STT model in background while user speaks so transcription is instant."""
    try:
        cfg = load_config()
        if cfg.get("engine") == "whisper":
            model_id = cfg.get("model_id", "Systran/faster-distil-whisper-small.en")
            device = cfg.get("device", "cuda")
            compute_type = cfg.get("compute_type", "int8_float32")
            get_whisper_model(model_id, device=device, compute_type=compute_type)
    except Exception as e:
        print(f"[InSitu STT] Preload warning: {e}", file=sys.stderr)

def convert_to_wav16k(input_file: str) -> str:
    if input_file.lower().endswith(".wav"):
        return input_file
    out_wav = tempfile.mktemp(suffix=".wav")
    cmd = [
        "ffmpeg", "-y", "-i", input_file,
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        out_wav
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return out_wav

def transcribe_audio_data(audio_data: np.ndarray) -> str:
    """Transcribes in-memory float32 audio data using the currently configured STT model."""
    cfg = load_config()
    model_id = cfg.get("model_id", "moonshine/tiny")
    engine = cfg.get("engine", "moonshine")
    prefix = cfg.get("prompt_prefix", "")
    device = cfg.get("device", "cpu")
    compute_type = cfg.get("compute_type", "int8")

    if engine == "whisper":
        model = get_whisper_model(model_id, device=device, compute_type=compute_type)
        segments, _ = model.transcribe(
            audio_data,
            initial_prompt=prefix if prefix else None,
            beam_size=1,
            language="en"
        )
        texts = [s.text.strip() for s in segments if s.text and s.text.strip()]
        return " ".join(texts)
    else:
        # Default Moonshine ONNX
        import moonshine_onnx
        result = moonshine_onnx.transcribe(audio_data, model=model_id)
        if isinstance(result, list) and len(result) > 0:
            return " ".join([r.strip() for r in result if r and r.strip()])
        elif isinstance(result, str):
            return result.strip()
        return ""

def transcribe_file(file_path: str) -> str:
    """Transcribes an audio file using the currently configured STT model."""
    cfg = load_config()
    engine = cfg.get("engine", "moonshine")
    device = cfg.get("device", "cpu")
    compute_type = cfg.get("compute_type", "int8")

    if engine == "whisper":
        model_id = cfg.get("model_id", "Systran/faster-whisper-tiny.en")
        prefix = cfg.get("prompt_prefix", "")
        model = get_whisper_model(model_id, device=device, compute_type=compute_type)
        segments, _ = model.transcribe(
            file_path,
            initial_prompt=prefix if prefix else None,
            beam_size=1,
            language="en"
        )
        texts = [s.text.strip() for s in segments if s.text and s.text.strip()]
        return " ".join(texts)
    else:
        import moonshine_onnx
        model_id = cfg.get("model_id", "moonshine/tiny")
        converted = None
        try:
            if not file_path.lower().endswith(".wav"):
                converted = convert_to_wav16k(file_path)
                target = converted
            else:
                target = file_path
            result = moonshine_onnx.transcribe(target, model=model_id)
            if isinstance(result, list) and len(result) > 0:
                return " ".join([r.strip() for r in result if r and r.strip()])
            elif isinstance(result, str):
                return result.strip()
            return ""
        finally:
            if converted and os.path.exists(converted):
                try:
                    os.remove(converted)
                except Exception:
                    pass

def record_with_vad(
    max_duration: float = 30.0,
    silence_autostop: bool = True,
    silence_seconds: float = 1.2,
    ctrl_file: str = CTRL_FILE
) -> dict:
    import sounddevice as sd

    # Reset control file
    try:
        if os.path.exists(ctrl_file):
            os.remove(ctrl_file)
    except Exception:
        pass

    # Start background model prewarming thread immediately
    threading.Thread(target=preload_active_model_async, daemon=True).start()

    sample_rate = 16000
    chunk_samples = 1024
    chunk_duration = chunk_samples / sample_rate

    recorded_chunks = []
    has_spoken = False
    consecutive_silence = 0.0
    speech_threshold = 0.012  # RMS threshold for voice activity

    start_time = time.time()

    def check_control():
        if os.path.exists(ctrl_file):
            try:
                with open(ctrl_file, "r") as f:
                    content = f.read().strip().upper()
                return content
            except Exception:
                pass
        return None

    try:
        with sd.InputStream(samplerate=sample_rate, channels=1, dtype="float32", blocksize=chunk_samples) as stream:
            while True:
                # 1. Read chunk
                chunk, overflowed = stream.read(chunk_samples)
                recorded_chunks.append(chunk.flatten())

                # 2. Check control commands (STOP / CANCEL)
                ctrl = check_control()
                if ctrl == "CANCEL":
                    print("[InSitu STT] Recording cancelled by user.", file=sys.stderr)
                    return {"cancelled": True}
                elif ctrl == "STOP":
                    break

                # 3. Calculate volume (RMS)
                rms = float(np.sqrt(np.mean(chunk**2)))

                if rms > speech_threshold:
                    has_spoken = True
                    consecutive_silence = 0.0
                else:
                    if has_spoken:
                        consecutive_silence += chunk_duration
                        if silence_autostop and consecutive_silence >= silence_seconds:
                            print(f"[VAD] {silence_seconds}s silence detected, auto-stopping.", file=sys.stderr)
                            break

                # 4. Check max duration timeout
                elapsed = time.time() - start_time
                if elapsed >= max_duration:
                    break

    finally:
        try:
            if os.path.exists(ctrl_file):
                os.remove(ctrl_file)
        except Exception:
            pass

    if not recorded_chunks:
        return {"text": ""}

    audio_data = np.concatenate(recorded_chunks, axis=0)

    # If audio is practically silent (< 0.25s of sound), return empty
    if len(audio_data) < sample_rate * 0.25:
        return {"text": ""}

    # Check cancel again before transcription
    if check_control() == "CANCEL":
        return {"cancelled": True}

    # Transcribe via active STT engine
    try:
        text = transcribe_audio_data(audio_data)
        return {"text": text}
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="InSitu STT Transcriber")
    parser.add_argument("file", nargs="?", default=None, help="Audio file to transcribe")
    parser.add_argument("--mic", action="store_true", help="Record from mic")
    parser.add_argument("--max-duration", type=float, default=25.0, help="Max duration in seconds")
    parser.add_argument("--no-silence-stop", action="store_true", help="Disable silence autostop")
    parser.add_argument("--silence-seconds", type=float, default=1.2, help="Seconds of silence to trigger autostop")
    parser.add_argument("--ctrl-file", type=str, default=CTRL_FILE, help="Control file path")

    args = parser.parse_args()

    if args.mic:
        silence_autostop = not args.no_silence_stop
        res = record_with_vad(
            max_duration=args.max_duration,
            silence_autostop=silence_autostop,
            silence_seconds=args.silence_seconds,
            ctrl_file=args.ctrl_file
        )
        print(json.dumps(res))
        if res.get("cancelled"):
            sys.exit(2)
    elif args.file:
        if not os.path.exists(args.file):
            print(json.dumps({"error": f"File not found: {args.file}"}))
            sys.exit(1)
        res_text = transcribe_file(args.file)
        print(json.dumps({"text": res_text}))
    else:
        # Default to mic
        res = record_with_vad(max_duration=25.0, silence_autostop=True, silence_seconds=1.2)
        print(json.dumps(res))
        if res.get("cancelled"):
            sys.exit(2)

