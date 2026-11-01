#!/usr/bin/env python3
"""
Socratic Tutor STT Transcriber with VAD, Dynamic Model Loading & Coding Prompt Bias
Features:
- Real-time audio streaming with 2s silence autostop (togglable)
- Control commands: STOP (manually finish) & CANCEL (abort)
- Dynamic model support: Moonshine ONNX & Faster-Whisper (CTranslate2 INT8)
- Coding vocabulary prompt bias for snake_case, kubectl, async/await, camelCase, etc.
"""

import sys
import os
import time
import json
import tempfile
import subprocess
import argparse
import numpy as np

# Import model manager
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model_manager import load_config

CTRL_FILE = "/tmp/insitu_mic_ctrl"

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

def get_whisper_model(model_id: str, device: str = "cpu", compute_type: str = "int8"):
    from faster_whisper import WhisperModel
    if device == "cuda":
        try:
            return WhisperModel(model_id, device="cuda", compute_type="float16")
        except Exception as e:
            print(f"[Socratic STT] Warning: CUDA error ({e}). Falling back to CPU.", file=sys.stderr)
            return WhisperModel(model_id, device="cpu", compute_type="int8")
    return WhisperModel(model_id, device="cpu", compute_type="int8")

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
    silence_seconds: float = 2.0,
    ctrl_file: str = CTRL_FILE
) -> dict:
    import sounddevice as sd

    # Reset control file
    try:
        if os.path.exists(ctrl_file):
            os.remove(ctrl_file)
    except Exception:
        pass

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
                            print(f"[VAD] 2s silence detected, auto-stopping.", file=sys.stderr)
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

    # If audio is practically silent (< 0.2s of actual sound), don't waste time
    if len(audio_data) < sample_rate * 0.4:
        return {"text": ""}

    # Transcribe via active STT engine
    try:
        text = transcribe_audio_data(audio_data)
        return {"text": text}
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Socratic STT Transcriber")
    parser.add_argument("file", nargs="?", default=None, help="Audio file to transcribe")
    parser.add_argument("--mic", action="store_true", help="Record from mic")
    parser.add_argument("--max-duration", type=float, default=25.0, help="Max duration in seconds")
    parser.add_argument("--no-silence-stop", action="store_true", help="Disable 2s silence autostop")
    parser.add_argument("--silence-seconds", type=float, default=2.0, help="Seconds of silence to trigger autostop")
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
    elif args.file:
        if not os.path.exists(args.file):
            print(json.dumps({"error": f"File not found: {args.file}"}))
            sys.exit(1)
        res_text = transcribe_file(args.file)
        print(json.dumps({"text": res_text}))
    else:
        # Default to mic
        res = record_with_vad(max_duration=25.0, silence_autostop=True, silence_seconds=2.0)
        print(json.dumps(res))
