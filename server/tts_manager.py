#!/usr/bin/env python3
"""
Socratic Tutor TTS Model & Voice Manager
Handles:
- Curated Tier List of Hugging Face TTS Models (Tier 1 to Tier 4)
- Preset-Voice Models (Kokoro-82M, Piper-Voices)
- Zero-Shot Voice Cloning Models (F5-TTS, XTTS-v2)
- Lazy downloading and Hugging Face Hub caching
- Voice preset selection & reference audio configuration
"""

import os
import sys
import json
import argparse
import urllib.request
from pathlib import Path
from typing import Dict, Any, List, Optional

CONFIG_FILE = "/home/Aelfwif/insitu/tts_config.json"
CACHE_DIR = os.path.expanduser("~/.cache/insitu-tts")
KOKORO_CACHE_DIR = os.path.expanduser("~/.cache/kokoro")
PIPER_MODELS_DIR = "/home/Aelfwif/insitu/server/models/piper"

PRESET_MODELS = [
    {
        "id": "hexgrad/Kokoro-82M",
        "name": "Kokoro 82M (hexgrad)",
        "tier": "Tier 1: Ultra-Lightweight (Low CPU / Raspberry Pi)",
        "engine": "kokoro",
        "repo_id": "onnx-community/Kokoro-82M-v1.0-ONNX",
        "params": "82M",
        "ram": "< 100 MB",
        "type": "preset",
        "recommended_for_weak_pc": True,
        "description": "Gold standard for lightweight TTS. 54 high-quality voices (US/UK/JA/ZH), near-commercial naturalness on pure CPU."
    },
    {
        "id": "rhasspy/piper-voices",
        "name": "Piper Voices (rhasspy)",
        "tier": "Tier 1: Ultra-Lightweight (Low CPU / Raspberry Pi)",
        "engine": "piper",
        "repo_id": "rhasspy/piper-voices",
        "params": "15M-60M",
        "ram": "< 50 MB",
        "type": "preset",
        "recommended_for_weak_pc": True,
        "description": "Ultra-low latency (<20ms), instant offline speech, 100+ voices across 30+ languages."
    },
    {
        "id": "mush42/mms-tts",
        "name": "Meta MMS-TTS (Meta AI)",
        "tier": "Tier 1: Ultra-Lightweight (Low CPU / Raspberry Pi)",
        "engine": "mms",
        "repo_id": "facebook/mms-tts",
        "params": "30M-50M",
        "ram": "< 80 MB",
        "type": "preset",
        "recommended_for_weak_pc": True,
        "description": "Meta's massive multilingual project with 7,000+ language voices on minimal hardware."
    },
    {
        "id": "SWivid/F5-TTS",
        "name": "F5-TTS Flow Matching (SWivid)",
        "tier": "Tier 3: Mid-High GPU (Dedicated Desktop / Workstation)",
        "engine": "f5-tts",
        "repo_id": "SWivid/F5-TTS",
        "params": "335M",
        "ram": "~3-4 GB",
        "type": "clone",
        "recommended_for_weak_pc": False,
        "description": "Top recommendation for Zero-Shot Voice Cloning. Provide a 3-10s .wav to clone any voice with flawless accent and cadence."
    },
    {
        "id": "coqui/XTTS-v2",
        "name": "XTTS-v2 Cross-Lingual (Coqui)",
        "tier": "Tier 2: Mid-Range (Laptops & M-Series Macs)",
        "engine": "xtts",
        "repo_id": "coqui/XTTS-v2",
        "params": "400M-500M",
        "ram": "~2-3 GB",
        "type": "clone",
        "recommended_for_weak_pc": False,
        "description": "Zero-shot cross-lingual voice cloning across 17 languages from a 6-second audio clip."
    },
    {
        "id": "yl4579/StyleTTS2-LibriTTS",
        "name": "StyleTTS 2 (yl4579)",
        "tier": "Tier 2: Mid-Range (Laptops & M-Series Macs)",
        "engine": "styletts2",
        "repo_id": "yl4579/StyleTTS2-LibriTTS",
        "params": "250M",
        "ram": "~2 GB",
        "type": "preset",
        "recommended_for_weak_pc": False,
        "description": "Fast diffusion-style inference with 100+ LibriTTS speakers and natural human cadence."
    },
    {
        "id": "suno/bark-small",
        "name": "Bark Small (Suno)",
        "tier": "Tier 2: Mid-Range (Laptops & M-Series Macs)",
        "engine": "bark",
        "repo_id": "suno/bark-small",
        "params": "300M",
        "ram": "~2-3 GB",
        "type": "preset",
        "recommended_for_weak_pc": False,
        "description": "GPT-style acoustic codec with 100+ presets and conversational effects (laughs, sighs)."
    },
    {
        "id": "canopylabs/orpheus-3b-0.1-ft",
        "name": "Orpheus 3B Audio LLM (Canopy Labs)",
        "tier": "Tier 4: High-End Workstation (12-24 GB+ GPU)",
        "engine": "orpheus",
        "repo_id": "canopylabs/orpheus-3b-0.1-ft",
        "params": "3B-4B",
        "ram": "~12-16 GB",
        "type": "clone",
        "recommended_for_weak_pc": False,
        "description": "Instruction-guided emotional and tone delivery with deep technical comprehension."
    }
]

KOKORO_VOICES = [
    # American Female
    {"id": "af_heart", "name": "Heart (Default Warm)", "accent": "American", "gender": "Female", "quality": "⭐⭐⭐⭐⭐"},
    {"id": "af_bella", "name": "Bella (Energetic & Clear)", "accent": "American", "gender": "Female", "quality": "⭐⭐⭐⭐⭐"},
    {"id": "af_sarah", "name": "Sarah (Articulate)", "accent": "American", "gender": "Female", "quality": "⭐⭐⭐⭐"},
    {"id": "af_nicole", "name": "Nicole (Smooth)", "accent": "American", "gender": "Female", "quality": "⭐⭐⭐⭐"},
    {"id": "af_sky", "name": "Sky (Soft & Calm)", "accent": "American", "gender": "Female", "quality": "⭐⭐⭐⭐"},
    {"id": "af_alloy", "name": "Alloy (Modern Assistant)", "accent": "American", "gender": "Female", "quality": "⭐⭐⭐⭐"},
    {"id": "af_jessica", "name": "Jessica (Professional)", "accent": "American", "gender": "Female", "quality": "⭐⭐⭐⭐"},
    {"id": "af_nova", "name": "Nova (Bright)", "accent": "American", "gender": "Female", "quality": "⭐⭐⭐⭐"},
    {"id": "af_river", "name": "River (Natural)", "accent": "American", "gender": "Female", "quality": "⭐⭐⭐⭐"},
    # American Male
    {"id": "am_adam", "name": "Adam (Deep & Confident)", "accent": "American", "gender": "Male", "quality": "⭐⭐⭐⭐⭐"},
    {"id": "am_michael", "name": "Michael (Technical Mentor)", "accent": "American", "gender": "Male", "quality": "⭐⭐⭐⭐⭐"},
    {"id": "am_echo", "name": "Echo (Balanced)", "accent": "American", "gender": "Male", "quality": "⭐⭐⭐⭐"},
    {"id": "am_eric", "name": "Eric (Conversational)", "accent": "American", "gender": "Male", "quality": "⭐⭐⭐⭐"},
    {"id": "am_liam", "name": "Liam (Youthful)", "accent": "American", "gender": "Male", "quality": "⭐⭐⭐⭐"},
    {"id": "am_onyx", "name": "Onyx (Rich Baritone)", "accent": "American", "gender": "Male", "quality": "⭐⭐⭐⭐"},
    {"id": "am_puck", "name": "Puck (Inquisitive)", "accent": "American", "gender": "Male", "quality": "⭐⭐⭐⭐"},
    # British Female
    {"id": "bf_emma", "name": "Emma (Refined British)", "accent": "British", "gender": "Female", "quality": "⭐⭐⭐⭐⭐"},
    {"id": "bf_isabella", "name": "Isabella (Melodic)", "accent": "British", "gender": "Female", "quality": "⭐⭐⭐⭐"},
    {"id": "bf_alice", "name": "Alice (Crisp Oxford)", "accent": "British", "gender": "Female", "quality": "⭐⭐⭐⭐"},
    {"id": "bf_lily", "name": "Lily (Gentle)", "accent": "British", "gender": "Female", "quality": "⭐⭐⭐⭐"},
    # British Male
    {"id": "bm_george", "name": "George (Classic British Mentor)", "accent": "British", "gender": "Male", "quality": "⭐⭐⭐⭐⭐"},
    {"id": "bm_daniel", "name": "Daniel (Dynamic)", "accent": "British", "gender": "Male", "quality": "⭐⭐⭐⭐"},
    {"id": "bm_lewis", "name": "Lewis (Scholarly)", "accent": "British", "gender": "Male", "quality": "⭐⭐⭐⭐"},
    {"id": "bm_fable", "name": "Fable (Expressive)", "accent": "British", "gender": "Male", "quality": "⭐⭐⭐⭐"},
    # Multilingual / Japanese / Mandarin
    {"id": "jf_alpha", "name": "Alpha (Japanese Female)", "accent": "Japanese", "gender": "Female", "quality": "⭐⭐⭐⭐"},
    {"id": "jm_kumo", "name": "Kumo (Japanese Male)", "accent": "Japanese", "gender": "Male", "quality": "⭐⭐⭐⭐"},
    {"id": "zf_xiaobei", "name": "Xiaobei (Mandarin Female)", "accent": "Mandarin", "gender": "Female", "quality": "⭐⭐⭐⭐"},
    {"id": "zm_yunxi", "name": "Yunxi (Mandarin Male)", "accent": "Mandarin", "gender": "Male", "quality": "⭐⭐⭐⭐"}
]

PIPER_VOICES = [
    {"id": "en_US-lessac-medium", "name": "Lessac (US Female - Clear & Balanced)", "accent": "American", "gender": "Female", "quality": "⭐⭐⭐⭐"},
    {"id": "en_GB-alan-medium", "name": "Alan (British Male - Low Pitch)", "accent": "British", "gender": "Male", "quality": "⭐⭐⭐⭐"},
    {"id": "en_US-amy-medium", "name": "Amy (US Female - Upbeat)", "accent": "American", "gender": "Female", "quality": "⭐⭐⭐⭐"},
    {"id": "en_US-bryce-medium", "name": "Bryce (US Male - Deep)", "accent": "American", "gender": "Male", "quality": "⭐⭐⭐⭐"},
    {"id": "en_US-joe-medium", "name": "Joe (US Male - Natural)", "accent": "American", "gender": "Male", "quality": "⭐⭐⭐⭐"},
    {"id": "en_US-ryan-medium", "name": "Ryan (US Male - Fast)", "accent": "American", "gender": "Male", "quality": "⭐⭐⭐⭐"}
]

def load_config() -> Dict[str, Any]:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "model_id": "hexgrad/Kokoro-82M",
        "engine": "kokoro",
        "voice_id": "af_heart",
        "speed": 1.0,
        "reference_audio": None
    }

def save_config(cfg: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"Error saving config: {e}", file=sys.stderr)

def is_model_cached(model_id: str) -> bool:
    if model_id == "hexgrad/Kokoro-82M":
        voices_bin = os.path.join(KOKORO_CACHE_DIR, "voices-v1.0.bin")
        hf_dir = os.path.expanduser("~/.cache/huggingface/hub/models--onnx-community--Kokoro-82M-v1.0-ONNX")
        return os.path.exists(voices_bin) and os.path.exists(hf_dir)
    elif model_id == "rhasspy/piper-voices":
        lessac = os.path.join(PIPER_MODELS_DIR, "en_US-lessac-medium.onnx")
        return os.path.exists(lessac)
    else:
        # Check generic Hugging Face cache
        clean_name = model_id.replace("/", "--")
        hf_dir = os.path.expanduser(f"~/.cache/huggingface/hub/models--{clean_name}")
        return os.path.exists(hf_dir)

def delete_model(model_id: str) -> Dict[str, Any]:
    import shutil
    freed = 0
    deleted_paths = []

    def get_dir_size(path: str) -> int:
        total = 0
        if os.path.isfile(path): return os.path.getsize(path)
        for root, dirs, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                if os.path.exists(fp):
                    total += os.path.getsize(fp)
        return total

    if model_id == "hexgrad/Kokoro-82M":
        paths = [
            os.path.join(KOKORO_CACHE_DIR, "voices-v1.0.bin"),
            os.path.expanduser("~/.cache/huggingface/hub/models--onnx-community--Kokoro-82M-v1.0-ONNX")
        ]
        for p in paths:
            if os.path.exists(p):
                freed += get_dir_size(p)
                if os.path.isdir(p): shutil.rmtree(p)
                else: os.remove(p)
                deleted_paths.append(p)
    elif model_id == "rhasspy/piper-voices":
        lessac = os.path.join(PIPER_MODELS_DIR, "en_US-lessac-medium.onnx")
        lessac_json = os.path.join(PIPER_MODELS_DIR, "en_US-lessac-medium.onnx.json")
        for p in [lessac, lessac_json]:
            if os.path.exists(p):
                freed += os.path.getsize(p)
                os.remove(p)
                deleted_paths.append(p)
    else:
        clean_name = model_id.replace("/", "--")
        hf_dir = os.path.expanduser(f"~/.cache/huggingface/hub/models--{clean_name}")
        if os.path.exists(hf_dir):
            freed += get_dir_size(hf_dir)
            shutil.rmtree(hf_dir)
            deleted_paths.append(hf_dir)

    cfg = load_config()
    if cfg.get("model_id") == model_id:
        cfg["model_id"] = "hexgrad/Kokoro-82M"
        cfg["engine"] = "kokoro"
        cfg["voice_id"] = "af_heart"
        save_config(cfg)

    mb = round(freed / (1024 * 1024), 1)
    return {"status": "ok", "model_id": model_id, "freed_mb": mb, "deleted_paths": deleted_paths}

def download_model(model_id: str) -> bool:
    print(f"PROGRESS: {{\"status\": \"starting\", \"percent\": 5, \"message\": \"Connecting to Hugging Face for {model_id}...\"}}", flush=True)

    if model_id == "hexgrad/Kokoro-82M":
        os.makedirs(KOKORO_CACHE_DIR, exist_ok=True)
        voices_file = os.path.join(KOKORO_CACHE_DIR, "voices-v1.0.bin")
        if not os.path.exists(voices_file):
            print(f"PROGRESS: {{\"status\": \"downloading\", \"percent\": 15, \"message\": \"Downloading Kokoro 54-voice bank (28MB)...\"}}", flush=True)
            url = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
            urllib.request.urlretrieve(url, voices_file)

        print(f"PROGRESS: {{\"status\": \"downloading\", \"percent\": 50, \"message\": \"Downloading Kokoro-82M ONNX weights (92MB)...\"}}", flush=True)
        from huggingface_hub import hf_hub_download
        hf_hub_download(
            repo_id="onnx-community/Kokoro-82M-v1.0-ONNX",
            filename="onnx/model_quantized.onnx"
        )
        print(f"PROGRESS: {{\"status\": \"complete\", \"percent\": 100, \"message\": \"Kokoro-82M ready for zero-latency CPU inference.\"}}", flush=True)
        return True

    elif model_id == "rhasspy/piper-voices":
        print(f"PROGRESS: {{\"status\": \"complete\", \"percent\": 100, \"message\": \"Piper voices ready in local server directory.\"}}", flush=True)
        return True

    else:
        # Generic Hugging Face snapshot download
        from huggingface_hub import snapshot_download
        print(f"PROGRESS: {{\"status\": \"downloading\", \"percent\": 40, \"message\": \"Downloading {model_id} from Hugging Face...\"}}", flush=True)
        snapshot_download(repo_id=model_id)
        print(f"PROGRESS: {{\"status\": \"complete\", \"percent\": 100, \"message\": \"{model_id} downloaded successfully.\"}}", flush=True)
        return True

def list_models_json() -> str:
    cfg = load_config()
    current_model = cfg.get("model_id", "hexgrad/Kokoro-82M")
    result = []
    for m in PRESET_MODELS:
        item = dict(m)
        item["is_current"] = (m["id"] == current_model)
        item["is_cached"] = is_model_cached(m["id"])
        result.append(item)
    return json.dumps(result, indent=2)

def list_voices_json() -> str:
    cfg = load_config()
    engine = cfg.get("engine", "kokoro")
    current_voice = cfg.get("voice_id", "af_heart")

    if engine == "kokoro":
        voices = KOKORO_VOICES
    else:
        voices = PIPER_VOICES

    result = []
    for v in voices:
        item = dict(v)
        item["is_current"] = (v["id"] == current_voice)
        result.append(item)
    return json.dumps(result, indent=2)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Socratic TTS Model Manager")
    parser.add_argument("--list-models", action="store_true", help="List all curated TTS models with status")
    parser.add_argument("--list-voices", action="store_true", help="List available voices for current engine")
    parser.add_argument("--set-model", type=str, help="Set active TTS model ID")
    parser.add_argument("--set-voice", type=str, help="Set active voice ID")
    parser.add_argument("--set-clone-audio", type=str, help="Set reference .wav for voice cloning")
    parser.add_argument("--download", type=str, help="Download model from Hugging Face with progress")
    parser.add_argument("--delete-model", type=str, help="Delete model cache and reclaim disk space")
    parser.add_argument("--status", action="store_true", help="Print current TTS configuration")

    args = parser.parse_args()

    if args.list_models:
        print(list_models_json())
    elif args.list_voices:
        print(list_voices_json())
    elif args.set_model:
        cfg = load_config()
        # Find engine
        engine = "kokoro"
        for m in PRESET_MODELS:
            if m["id"] == args.set_model:
                engine = m["engine"]
                break
        cfg["model_id"] = args.set_model
        cfg["engine"] = engine
        # Reset default voice
        if engine == "kokoro":
            cfg["voice_id"] = "af_heart"
        elif engine == "piper":
            cfg["voice_id"] = "en_US-lessac-medium"
        save_config(cfg)
        print(json.dumps({"status": "ok", "model_id": args.set_model, "engine": engine, "voice_id": cfg["voice_id"]}))
    elif args.set_voice:
        cfg = load_config()
        cfg["voice_id"] = args.set_voice
        save_config(cfg)
        print(json.dumps({"status": "ok", "voice_id": args.set_voice}))
    elif args.set_clone_audio:
        cfg = load_config()
        cfg["reference_audio"] = args.set_clone_audio
        save_config(cfg)
        print(json.dumps({"status": "ok", "reference_audio": args.set_clone_audio}))
    elif args.download:
        ok = download_model(args.download)
        print(json.dumps({"status": "ok" if ok else "error", "model_id": args.download}))
    elif args.delete_model:
        res = delete_model(args.delete_model)
        print(json.dumps(res))
    elif args.status:
        cfg = load_config()
        print(json.dumps(cfg, indent=2))
    else:
        parser.print_help()
