#!/usr/bin/env python3
"""
Socratic Tutor STT Model Manager
Handles:
- Curated tier list of open-source Hugging Face STT models (1GB, 2GB, 3GB hardware profiles)
- Hardware acceleration toggle (CPU / GPU CUDA) with automatic fallback
- Lazy loading and on-disk caching
- Live download progress streaming (for VS Code progress bar)
- Coding vocabulary prompt bias configuration
"""

import os
import sys
import json
import argparse
from typing import Dict, Any, List, Optional
import huggingface_hub
from tqdm.auto import tqdm

# Preload NVIDIA CUDA libraries from pip packages if available
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

CONFIG_FILE = "/home/Aelfwif/insitu/stt_config.json"
MODELS_CACHE_DIR = "/home/Aelfwif/.cache/huggingface/hub"

DEFAULT_CODE_PREFIX = (
    "JavaScript, TypeScript, Python, C++, Rust, Go, "
    "snake_case, camelCase, PascalCase, kebab-case, "
    "kubectl, async/await, const, let, fn, def, struct, impl, "
    "return, import, from, git, PR, API, JSON, GraphQL, SQL, Docker"
)

PRESET_MODELS = [
    # Tier 1: Ultra-Lightweight (< 500 MB RAM / Low-End CPU / Edge)
    {
        "id": "moonshine/tiny",
        "name": "Moonshine Tiny (UsefulSensors)",
        "tier": "Tier 1: Ultra-Lightweight (< 500 MB RAM / Edge)",
        "engine": "moonshine",
        "repo_id": "UsefulSensors/moonshine-tiny",
        "params": "27M",
        "ram": "< 100 MB",
        "vram": "N/A",
        "model_size": "50 MB",
        "recommended_for_weak_pc": True,
        "description": "Ultra-low latency (~50ms CPU), near-zero RAM. Best default for weak PCs."
    },
    {
        "id": "moonshine/base",
        "name": "Moonshine Base (UsefulSensors)",
        "tier": "Tier 1: Ultra-Lightweight (< 500 MB RAM / Edge)",
        "engine": "moonshine",
        "repo_id": "UsefulSensors/moonshine-base",
        "params": "61M",
        "ram": "~150 MB",
        "vram": "N/A",
        "model_size": "120 MB",
        "recommended_for_weak_pc": True,
        "description": "Higher accuracy English edge model with fast CPU inference."
    },
    {
        "id": "Systran/faster-whisper-tiny.en",
        "name": "Whisper Tiny.en (OpenAI / CTranslate2)",
        "tier": "Tier 1: Ultra-Lightweight (< 500 MB RAM / Edge)",
        "engine": "whisper",
        "repo_id": "Systran/faster-whisper-tiny.en",
        "params": "39M",
        "ram": "~150 MB",
        "vram": "~250 MB",
        "model_size": "72 MB",
        "recommended_for_weak_pc": True,
        "description": "Fast Whisper with coding prefix bias. Runs on any CPU without lag."
    },
    {
        "id": "Systran/faster-whisper-base.en",
        "name": "Whisper Base.en (OpenAI / CTranslate2)",
        "tier": "Tier 1: Ultra-Lightweight (< 500 MB RAM / Edge)",
        "engine": "whisper",
        "repo_id": "Systran/faster-whisper-base.en",
        "params": "74M",
        "ram": "~250 MB",
        "vram": "~400 MB",
        "model_size": "138 MB",
        "recommended_for_weak_pc": True,
        "description": "Solid English dictation balance with coding vocabulary support."
    },

    # Tier 2: 1 GB Models (Fast CPU & 1-2 GB VRAM GPUs)
    {
        "id": "Systran/faster-distil-whisper-small.en",
        "name": "Distil-Whisper Small.en (Hugging Face)",
        "tier": "Tier 2: 1 GB Models (Fast CPU & Entry GPU)",
        "engine": "whisper",
        "repo_id": "Systran/faster-distil-whisper-small.en",
        "params": "166M",
        "ram": "~500 MB",
        "vram": "~600 MB",
        "model_size": "316 MB",
        "recommended_for_weak_pc": True,
        "description": "5-6x faster than standard Whisper, exceptional technical coding accuracy."
    },
    {
        "id": "Systran/faster-whisper-small.en",
        "name": "Whisper Small.en (OpenAI / CTranslate2)",
        "tier": "Tier 2: 1 GB Models (Fast CPU & Entry GPU)",
        "engine": "whisper",
        "repo_id": "Systran/faster-whisper-small.en",
        "params": "244M",
        "ram": "~800 MB - 1 GB",
        "vram": "~1.0 GB",
        "model_size": "461 MB",
        "recommended_for_weak_pc": False,
        "description": "High accuracy on complex syntax and naming. ~1 GB memory footprint."
    },
    {
        "id": "Systran/faster-whisper-small",
        "name": "Whisper Small Multilingual (OpenAI / CTranslate2)",
        "tier": "Tier 2: 1 GB Models (Fast CPU & Entry GPU)",
        "engine": "whisper",
        "repo_id": "Systran/faster-whisper-small",
        "params": "244M",
        "ram": "~1.0 GB",
        "vram": "~1.0 GB",
        "model_size": "461 MB",
        "recommended_for_weak_pc": False,
        "description": "Multilingual 1 GB model covering 99 languages and programming accents."
    },
    {
        "id": "Systran/faster-distil-whisper-medium.en",
        "name": "Distil-Whisper Medium.en (Hugging Face)",
        "tier": "Tier 2: 1 GB Models (Fast CPU & Entry GPU)",
        "engine": "whisper",
        "repo_id": "Systran/faster-distil-whisper-medium.en",
        "params": "394M",
        "ram": "~1.0 GB",
        "vram": "~1.0 GB",
        "model_size": "752 MB",
        "recommended_for_weak_pc": False,
        "description": "True 1 GB sweet spot. Retains Medium model precision with 4x faster speed."
    },

    # Tier 3: 2 GB Models (Dedicated 2-3 GB GPUs like GTX 1050/1650 & M-Series Macs)
    {
        "id": "deepdml/faster-whisper-large-v3-turbo-ct2",
        "name": "Whisper Large-v3 Turbo (OpenAI / deepdml CT2)",
        "tier": "Tier 3: 2 GB Models (Dedicated 2-3 GB GPU & Fast CPU)",
        "engine": "whisper",
        "repo_id": "deepdml/faster-whisper-large-v3-turbo-ct2",
        "params": "809M",
        "ram": "~1.5 - 2.0 GB",
        "vram": "~1.8 GB",
        "model_size": "1.5 GB",
        "recommended_for_weak_pc": False,
        "description": "Top recommendation for 2 GB hardware. Slashes decoder layers, giving Large-v3 quality at 6x speed."
    },
    {
        "id": "Systran/faster-distil-whisper-large-v3",
        "name": "Distil-Whisper Large-v3 (Hugging Face)",
        "tier": "Tier 3: 2 GB Models (Dedicated 2-3 GB GPU & Fast CPU)",
        "engine": "whisper",
        "repo_id": "Systran/faster-distil-whisper-large-v3",
        "params": "756M",
        "ram": "~1.8 - 2.0 GB",
        "vram": "~2.0 GB",
        "model_size": "1.4 GB",
        "recommended_for_weak_pc": False,
        "description": "English-focused 2 GB giant: retains 99% of Large-v3 accuracy, highly optimized."
    },
    {
        "id": "Systran/faster-whisper-medium.en",
        "name": "Whisper Medium.en (OpenAI / CTranslate2)",
        "tier": "Tier 3: 2 GB Models (Dedicated 2-3 GB GPU & Fast CPU)",
        "engine": "whisper",
        "repo_id": "Systran/faster-whisper-medium.en",
        "params": "769M",
        "ram": "~2.0 GB",
        "vram": "~2.0 GB",
        "model_size": "1.45 GB",
        "recommended_for_weak_pc": False,
        "description": "English Medium model. Highly accurate on code syntax, handles non-native accents well."
    },
    {
        "id": "Systran/faster-whisper-medium",
        "name": "Whisper Medium Multilingual (OpenAI / CTranslate2)",
        "tier": "Tier 3: 2 GB Models (Dedicated 2-3 GB GPU & Fast CPU)",
        "engine": "whisper",
        "repo_id": "Systran/faster-whisper-medium",
        "params": "769M",
        "ram": "~2.0 GB",
        "vram": "~2.0 GB",
        "model_size": "1.45 GB",
        "recommended_for_weak_pc": False,
        "description": "Multilingual 2 GB model with high accuracy across all programming domains."
    },

    # Tier 4: 3 GB Models (Maximized for 3 GB+ GPUs like GTX 1050 3GB / RTX 3050)
    {
        "id": "Systran/faster-whisper-large-v3",
        "name": "Whisper Large-v3 (OpenAI / CTranslate2)",
        "tier": "Tier 4: 3 GB Models (3 GB+ Dedicated GPU / High-End CPU)",
        "engine": "whisper",
        "repo_id": "Systran/faster-whisper-large-v3",
        "params": "1.55B",
        "ram": "~3.0 GB",
        "vram": "~2.9 - 3.2 GB",
        "model_size": "2.9 GB",
        "recommended_for_weak_pc": False,
        "description": "Benchmark state-of-the-art accuracy. Perfectly fits 3 GB VRAM GPUs in INT8/FP16 for maximum coding precision."
    }
]

def load_config() -> Dict[str, Any]:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                return data
        except Exception:
            pass
    # Default fallback
    return {
        "model_id": "moonshine/tiny",
        "engine": "moonshine",
        "prompt_prefix": DEFAULT_CODE_PREFIX,
        "device": "cpu",
        "compute_type": "int8"
    }

def save_config(cfg: Dict[str, Any]) -> None:
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"Error saving config: {e}", file=sys.stderr)

def get_dir_size(path: str) -> int:
    """Calculates true disk size by resolving symlinks and tracking inodes to avoid double counting."""
    total = 0
    seen_inodes = set()
    if os.path.isfile(path):
        return os.path.getsize(os.path.realpath(path))
    for root, dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                real_fp = os.path.realpath(fp)
                if os.path.exists(real_fp):
                    st = os.stat(real_fp)
                    key = (st.st_dev, st.st_ino)
                    if key not in seen_inodes:
                        seen_inodes.add(key)
                        total += st.st_size
            except OSError:
                pass
    return total

def get_model_disk_size(model_id: str, engine: str) -> Optional[int]:
    if engine == "moonshine":
        clean_name = model_id.split("/")[-1]
        p1 = os.path.expanduser(f"~/.cache/moonshine/{clean_name}")
        p2 = os.path.expanduser(f"~/.cache/huggingface/hub/models--UsefulSensors--{clean_name}")
        total = 0
        found = False
        for p in [p1, p2]:
            if os.path.exists(p):
                total += get_dir_size(p)
                found = True
        return total if found else None
    else:
        repo_id = model_id
        for m in PRESET_MODELS:
            if m["id"] == model_id:
                repo_id = m["repo_id"]
                break
        clean_repo = repo_id.replace("/", "--")
        hf_path = os.path.join(MODELS_CACHE_DIR, f"models--{clean_repo}")
        if os.path.exists(hf_path):
            return get_dir_size(hf_path)
    return None

def is_model_cached(model_id: str, engine: str) -> bool:
    if engine == "moonshine":
        clean_name = model_id.split("/")[-1]
        cache_path = os.path.expanduser(f"~/.cache/moonshine/{clean_name}")
        hf_cache = os.path.expanduser(f"~/.cache/huggingface/hub/models--UsefulSensors--{clean_name}")
        return os.path.exists(cache_path) or os.path.exists(hf_cache) or model_id == "moonshine/tiny"
    else:
        repo_id = model_id
        for m in PRESET_MODELS:
            if m["id"] == model_id:
                repo_id = m["repo_id"]
                break
        clean_repo = repo_id.replace("/", "--")
        hf_path = os.path.join(MODELS_CACHE_DIR, f"models--{clean_repo}")
        if not os.path.exists(hf_path):
            return False
        # Check that actual weight files (> 5 MB) are present
        for root, dirs, files in os.walk(hf_path):
            for f in files:
                if f.endswith(".bin") or f.endswith(".safetensors") or f.endswith(".onnx"):
                    fp = os.path.join(root, f)
                    try:
                        real_fp = os.path.realpath(fp)
                        if os.path.exists(real_fp) and os.path.getsize(real_fp) > 5 * 1024 * 1024:
                            return True
                    except OSError:
                        pass
        return False

def delete_model(model_id: str) -> Dict[str, Any]:
    """Deletes cached model files from disk to reclaim space."""
    target_engine = "whisper"
    repo_id = model_id
    for m in PRESET_MODELS:
        if m["id"] == model_id:
            target_engine = m["engine"]
            repo_id = m["repo_id"]
            break

    deleted_paths = []
    freed_bytes = 0
    import shutil

    if target_engine == "moonshine":
        clean_name = model_id.split("/")[-1]
        paths = [
            os.path.expanduser(f"~/.cache/moonshine/{clean_name}"),
            os.path.expanduser(f"~/.cache/huggingface/hub/models--UsefulSensors--{clean_name}")
        ]
        for p in paths:
            if os.path.exists(p):
                size = get_dir_size(p)
                freed_bytes += size
                try:
                    if os.path.isdir(p):
                        shutil.rmtree(p)
                    else:
                        os.remove(p)
                    deleted_paths.append(p)
                except Exception as e:
                    print(f"Error removing {p}: {e}", file=sys.stderr)
    else:
        clean_repo = repo_id.replace("/", "--")
        hf_path = os.path.join(MODELS_CACHE_DIR, f"models--{clean_repo}")
        if os.path.exists(hf_path):
            size = get_dir_size(hf_path)
            freed_bytes += size
            try:
                shutil.rmtree(hf_path)
                deleted_paths.append(hf_path)
            except Exception as e:
                print(f"Error removing {hf_path}: {e}", file=sys.stderr)

    # If deleted model was the currently active model, fallback to moonshine/tiny
    cfg = load_config()
    if cfg.get("model_id") == model_id or cfg.get("model_id") == repo_id:
        cfg["model_id"] = "moonshine/tiny"
        cfg["engine"] = "moonshine"
        save_config(cfg)

    mb = round(freed_bytes / (1024 * 1024), 1)
    return {
        "status": "ok",
        "model_id": model_id,
        "freed_bytes": freed_bytes,
        "freed_mb": mb,
        "deleted_paths": deleted_paths,
        "message": f"Deleted model {model_id} cache ({mb} MB reclaimed)."
    }

def list_models() -> Dict[str, Any]:
    cfg = load_config()
    current_id = cfg.get("model_id", "moonshine/tiny")
    device = cfg.get("device", "cpu")
    compute_type = cfg.get("compute_type", "int8")
    models_info = []

    for m in PRESET_MODELS:
        item = dict(m)
        item["is_active"] = (item["id"] == current_id or item["repo_id"] == current_id)
        cached = is_model_cached(item["id"], item["engine"])
        item["is_cached"] = cached
        disk_bytes = get_model_disk_size(item["id"], item["engine"]) if cached else None
        item["disk_bytes"] = disk_bytes
        item["disk_size_mb"] = f"{round(disk_bytes / (1024 * 1024), 1)} MB" if disk_bytes else None
        models_info.append(item)

    preset_ids = [p["id"] for p in PRESET_MODELS] + [p["repo_id"] for p in PRESET_MODELS]
    if current_id not in preset_ids:
        models_info.append({
            "id": current_id,
            "name": f"Custom: {current_id}",
            "tier": "Custom Hugging Face Model",
            "engine": cfg.get("engine", "whisper"),
            "repo_id": current_id,
            "params": "Custom",
            "ram": "Unknown",
            "vram": "Unknown",
            "model_size": "Custom",
            "recommended_for_weak_pc": False,
            "description": f"User-specified Hugging Face model ({current_id}).",
            "is_active": True,
            "is_cached": is_model_cached(current_id, cfg.get("engine", "whisper"))
        })

    return {
        "current_model": current_id,
        "current_engine": cfg.get("engine", "moonshine"),
        "device": device,
        "compute_type": compute_type,
        "prompt_prefix": cfg.get("prompt_prefix", DEFAULT_CODE_PREFIX),
        "models": models_info
    }

class ProgressReporter(tqdm):
    """Streams JSON progress updates to stdout for VS Code withProgress hook."""
    def __init__(self, *args, **kwargs):
        kwargs["file"] = sys.stderr
        super().__init__(*args, **kwargs)
        self.last_reported = -1

    def update(self, n=1):
        super().update(n)
        if self.total and self.total > 0:
            pct = min(100, int(self.n * 100 / self.total))
            if pct != self.last_reported:
                self.last_reported = pct
                msg = {
                    "type": "progress",
                    "progress": pct,
                    "desc": self.desc or "Downloading",
                    "downloaded_bytes": self.n,
                    "total_bytes": self.total
                }
                print(json.dumps(msg), flush=True)

    def refresh(self, *args, **kwargs):
        pass

    def set_description_str(self, *args, **kwargs):
        pass

def download_model(model_id: str) -> Dict[str, Any]:
    """Downloads model from Hugging Face with progress streaming."""
    target_engine = "whisper"
    repo_id = model_id

    for m in PRESET_MODELS:
        if m["id"] == model_id:
            target_engine = m["engine"]
            repo_id = m["repo_id"]
            break

    print(json.dumps({
        "type": "start",
        "model_id": model_id,
        "repo_id": repo_id,
        "engine": target_engine,
        "message": f"Starting download for {repo_id} from Hugging Face..."
    }), flush=True)

    if target_engine == "moonshine":
        import moonshine_onnx
        import numpy as np
        dummy_audio = np.zeros(16000, dtype=np.float32)
        print(json.dumps({"type": "progress", "progress": 50, "desc": f"Initializing {model_id}..."}), flush=True)
        moonshine_onnx.transcribe(dummy_audio, model=model_id)
        print(json.dumps({"type": "progress", "progress": 100, "desc": "Moonshine model ready."}), flush=True)
        return {"status": "ok", "model_id": model_id, "cached": True}
    else:
        # Check alias / short name mappings
        whisper_alias_map = {
            "openai/whisper-tiny": "Systran/faster-whisper-tiny",
            "openai/whisper-tiny.en": "Systran/faster-whisper-tiny.en",
            "openai/whisper-base": "Systran/faster-whisper-base",
            "openai/whisper-base.en": "Systran/faster-whisper-base.en",
            "openai/whisper-small": "Systran/faster-whisper-small",
            "openai/whisper-small.en": "Systran/faster-whisper-small.en",
            "openai/whisper-medium": "Systran/faster-whisper-medium",
            "openai/whisper-medium.en": "Systran/faster-whisper-medium.en",
            "openai/whisper-large-v3": "Systran/faster-whisper-large-v3",
            "openai/whisper-large-v3-turbo": "Systran/faster-whisper-large-v3-turbo",
            "distil-whisper/distil-small.en": "Systran/faster-distil-whisper-small.en",
            "distil-whisper/distil-medium.en": "Systran/faster-distil-whisper-medium.en",
            "distil-whisper/distil-large-v3": "Systran/faster-distil-whisper-large-v3",
        }
        actual_repo = whisper_alias_map.get(repo_id, repo_id)

        allow_patterns = [
            "config.json",
            "preprocessor_config.json",
            "model.bin",
            "model.safetensors",
            "tokenizer.json",
            "vocabulary.*",
            "*.json",
            "*.txt"
        ]

        path = None
        try:
            path = huggingface_hub.snapshot_download(
                repo_id=actual_repo,
                allow_patterns=allow_patterns,
                tqdm_class=ProgressReporter
            )
        except Exception as e_snap:
            print(f"[Model Manager] snapshot_download warning: {e_snap}. Trying faster_whisper native download...", file=sys.stderr)
            try:
                import faster_whisper
                path = faster_whisper.download_model(actual_repo)
            except Exception as e_fw:
                print(f"[Model Manager] faster_whisper fallback failed: {e_fw}. Retrying unrestricted snapshot_download...", file=sys.stderr)
                path = huggingface_hub.snapshot_download(
                    repo_id=actual_repo,
                    tqdm_class=ProgressReporter
                )

        print(json.dumps({
            "type": "complete",
            "progress": 100,
            "desc": f"Model {actual_repo} successfully downloaded and cached.",
            "path": path
        }), flush=True)

        return {"status": "ok", "model_id": model_id, "path": path}

def set_active_model(model_id: str, auto_download: bool = True) -> Dict[str, Any]:
    cfg = load_config()
    target_engine = "whisper"
    for m in PRESET_MODELS:
        if m["id"] == model_id:
            target_engine = m["engine"]
            break

    if "moonshine" in model_id.lower():
        target_engine = "moonshine"

    cached = is_model_cached(model_id, target_engine)

    if not cached and auto_download:
        download_result = download_model(model_id)
        if download_result.get("status") != "ok":
            return download_result

    cfg["model_id"] = model_id
    cfg["engine"] = target_engine
    save_config(cfg)

    return {
        "status": "ok",
        "model_id": model_id,
        "engine": target_engine,
        "device": cfg.get("device", "cpu"),
        "cached": True
    }

def get_best_cuda_compute_type() -> str:
    """Returns the optimal supported compute type for the active CUDA GPU."""
    try:
        import ctranslate2
        supported = ctranslate2.get_supported_compute_types("cuda")
        for pref in ["float16", "int8_float32", "int8", "float32"]:
            if pref in supported:
                return pref
    except Exception:
        pass
    return "int8_float32"

def toggle_device() -> Dict[str, Any]:
    cfg = load_config()
    current = cfg.get("device", "cpu")
    new_device = "cuda" if current == "cpu" else "cpu"
    cfg["device"] = new_device
    if new_device == "cuda":
        cfg["compute_type"] = get_best_cuda_compute_type()
    else:
        cfg["compute_type"] = "int8"
    save_config(cfg)
    return {
        "status": "ok",
        "device": new_device,
        "compute_type": cfg["compute_type"]
    }

def set_device(dev: str) -> Dict[str, Any]:
    cfg = load_config()
    dev = dev.lower().strip()
    if dev not in ["cpu", "cuda"]:
        dev = "cpu"
    cfg["device"] = dev
    if dev == "cuda":
        cfg["compute_type"] = get_best_cuda_compute_type()
    else:
        cfg["compute_type"] = "int8"
    save_config(cfg)
    return {
        "status": "ok",
        "device": dev,
        "compute_type": cfg["compute_type"]
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="InSitu STT Model Manager")
    parser.add_argument("action", choices=["list", "get", "set", "download", "delete", "set-prefix", "toggle-device", "set-device"], help="Action to perform")
    parser.add_argument("--model", type=str, default="", help="Model ID or Hugging Face repo ID")
    parser.add_argument("--prefix", type=str, default="", help="Coding terms prompt prefix")
    parser.add_argument("--device", type=str, default="cpu", help="Device (cpu or cuda)")

    args = parser.parse_args()

    if args.action == "list":
        print(json.dumps(list_models(), indent=2))
    elif args.action == "get":
        print(json.dumps(load_config(), indent=2))
    elif args.action == "set":
        if not args.model:
            print(json.dumps({"error": "--model required for set action"}))
            sys.exit(1)
        res = set_active_model(args.model)
        print(json.dumps(res))
    elif args.action == "download":
        if not args.model:
            print(json.dumps({"error": "--model required for download action"}))
            sys.exit(1)
        res = download_model(args.model)
        print(json.dumps(res))
    elif args.action == "delete":
        if not args.model:
            print(json.dumps({"error": "--model required for delete action"}))
            sys.exit(1)
        res = delete_model(args.model)
        print(json.dumps(res))
    elif args.action == "set-prefix":
        if not args.prefix:
            print(json.dumps({"error": "--prefix required for set-prefix action"}))
            sys.exit(1)
        cfg = load_config()
        cfg["prompt_prefix"] = args.prefix
        save_config(cfg)
        print(json.dumps({"status": "ok", "prompt_prefix": args.prefix}))
    elif args.action == "toggle-device":
        res = toggle_device()
        print(json.dumps(res))
    elif args.action == "set-device":
        res = set_device(args.device)
        print(json.dumps(res))
