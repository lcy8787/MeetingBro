"""Cross-platform pinned build and verified download for Cantonese GGUF."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "78ffbaa18533c2ed51eab2ef5ae0702330efc3e9"
REVISION = "b83510c7c0951b3b46a03aa9b5e60148c7f44666"
# SHA-256 values from the pinned revision's Hugging Face LFS metadata.
FILES = {
    "Qwen3-ASR-1.7B-Cantonese-HK.Q4_K_M.gguf": "abaeaa1c63a672f0bd7eb2862d11ec20c1422efc72b528febe828accb9dbe15b",
    "Qwen3-ASR-1.7B-Cantonese-HK.mmproj-Q8_0.gguf": "41335001caef035a1fd426a5409b4825a5804b0908937cf73e64d1b2a30745b5",
}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cuda", action="store_true", help="Build with the installed NVIDIA CUDA toolkit")
    args = parser.parse_args()
    bundled_cmake = ROOT / ".runtime" / "gguf-build-tools" / "cmake" / "data" / "bin"
    if bundled_cmake.is_dir():
        os.environ["PATH"] = str(bundled_cmake) + os.pathsep + os.environ.get("PATH", "")
    for tool in ("git", "cmake"):
        if not shutil.which(tool):
            raise RuntimeError(f"Install {tool} first. macOS: brew install cmake git; Linux: install git cmake build-essential; Windows: install Git, CMake and Visual Studio C++ Build Tools.")
    if args.cuda and not shutil.which("nvcc"):
        raise RuntimeError("Install the NVIDIA CUDA toolkit before using --cuda")
    runtime = ROOT / ".runtime" / "llama-gguf"
    source = runtime / "source"
    build = runtime / "build"
    if not source.exists():
        source.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "remote", "add", "origin", "https://github.com/ggml-org/llama.cpp.git"], check=True)
    revision_exists = subprocess.run(["git", "-C", str(source), "cat-file", "-e", COMMIT], capture_output=True).returncode == 0
    if not revision_exists:
        subprocess.run(["git", "-C", str(source), "fetch", "--depth", "1", "origin", COMMIT], check=True)
    subprocess.run(["git", "-C", str(source), "checkout", "--detach", COMMIT], check=True)
    subprocess.run(["cmake", "-S", str(source), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release",
        "-DLLAMA_CURL=OFF", "-DLLAMA_BUILD_TESTS=OFF", "-DLLAMA_BUILD_EXAMPLES=OFF",
        "-DLLAMA_BUILD_SERVER=ON", f"-DGGML_CUDA={'ON' if args.cuda else 'OFF'}",
        f"-DGGML_METAL={'ON' if platform.system() == 'Darwin' and platform.machine() == 'arm64' else 'OFF'}"], check=True)
    subprocess.run(["cmake", "--build", str(build), "--config", "Release", "--target", "llama-server", "--parallel", "2"], check=True)
    binary_name = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    binaries = list((build / "bin").rglob(binary_name))
    if not binaries:
        raise RuntimeError("Build produced no llama-server executable")
    shutil.copytree(binaries[0].parent, runtime / "bin", dirs_exist_ok=True)
    models = ROOT / "models" / "Qwen3-ASR-1.7B-Cantonese-HK-GGUF"
    models.mkdir(parents=True, exist_ok=True)
    for name, expected in FILES.items():
        target = models / name
        if target.exists() and digest(target) == expected:
            continue
        partial = target.with_suffix(".gguf.partial")
        if partial.exists() and digest(partial) == expected:
            partial.replace(target)
            continue
        url = f"https://huggingface.co/joe2643/Qwen3-ASR-1.7B-Cantonese-HK-GGUF/resolve/{REVISION}/{name}"
        print(f"Downloading {name}", flush=True)
        with urlopen(url, timeout=120) as response, partial.open("wb") as output:
            shutil.copyfileobj(response, output)
        if digest(partial) != expected:
            raise RuntimeError(f"Checksum mismatch: {partial}. Run installer again to retry.")
        partial.replace(target)
    (runtime / "installation.json").write_text(json.dumps({"commit": COMMIT, "revision": REVISION, "cuda": args.cuda}))
    backend = ROOT / "app" / "backend"
    sys.path.insert(0, str(backend))
    from meetingbro.asr.qwen3_gguf_adapter import Qwen3GGUFAdapter
    import numpy as np
    adapter = Qwen3GGUFAdapter()
    try:
        adapter.ensure_loaded()
        adapter.transcribe(np.zeros(16000, dtype=np.float32), 16000)
    finally:
        adapter.close()
    print("Installed and tested. Select 香港粵語 1.7B · GGUF Q4_K_M in Settings > Models.")


if __name__ == "__main__":
    main()
