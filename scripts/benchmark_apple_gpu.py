"""Replay local fixtures through the production session pipeline in real time.

No downloads and no microphone capture. Results and the disposable meeting DB
are kept under .runtime/benchmark. Use --seconds 900 for a sustained run.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import fields
import json
import logging
import os
from pathlib import Path
import resource
import sys
import time

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app/backend"))
from meetingbro.audio.capture import AudioChunk, AudioSource, _resample_mono
from meetingbro.asr.factory import build_formal_asr
from meetingbro.hardware import detect_hardware_profile
from meetingbro.llm.openai_compatible import _load_dotenv_if_present
from meetingbro.main import _build_preview_asr
from meetingbro.session.manager import SessionConfig, SessionManager
from meetingbro.session.profiles import runtime_profile_defaults
from meetingbro.storage.db import Storage
from meetingbro.summarization.heuristic import HeuristicSummarizer
from meetingbro.translation.passthrough import PassthroughTranslator


class Replay(AudioSource):
    def __init__(self, paths, seconds):
        clips = []
        for path in paths:
            samples, rate = sf.read(path, dtype="float32", always_2d=True)
            clips.extend([_resample_mono(samples.mean(axis=1), rate, 16000), np.zeros(16000, dtype=np.float32)])
        self.audio = np.concatenate(clips)
        self.seconds = seconds
        self.closed = False

    @property
    def sample_rate(self):
        return 16000

    async def stream(self):
        start = time.monotonic()
        frames = min(8000, int(self.seconds * 16000))
        count = int(self.seconds * 16000)
        for offset in range(0, count, frames):
            if self.closed:
                return
            indices = np.arange(offset, min(offset + frames, count)) % len(self.audio)
            yield AudioChunk(samples=self.audio[indices], sample_rate=16000, start_time=offset / 16000)
            await asyncio.sleep(max(0, min(offset + frames, count) / 16000 - (time.monotonic() - start)))

    async def aclose(self):
        self.closed = True


def percentiles(samples):
    return {"count": len(samples), "p50": float(np.median(samples)), "p95": float(np.percentile(samples, 95)), "max": max(samples)} if samples else {"count": 0}


async def main(args):
    os.chdir(ROOT / "app/backend")
    _load_dotenv_if_present()
    os.chdir(ROOT)
    os.environ.update(MEETINGBRO_WHISPER_ENGINE="whisper-cpp", MEETINGBRO_WHISPER_DEVICE="metal",
                      MEETINGBRO_WHISPER_SIZE="small", MEETINGBRO_PREVIEW_ASR_BACKEND=args.preview,
                      HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    hardware = detect_hardware_profile()
    asr, engine, device, _ = build_formal_asr(hardware)
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    storage = Storage(output.with_suffix(".db"))
    try:
        asr.ensure_loaded()
        preview = _build_preview_asr(hardware, formal_asr=asr) if args.preview != "shared" else None
        if hasattr(preview, "prewarm"):
            preview.prewarm()
        preset = runtime_profile_defaults("balanced")
        supported = {f.name for f in fields(SessionConfig)}
        preset = {k: v for k, v in preset.items() if k in supported}
        preset.update(asr_accumulation_seconds=2, pre_vad_max_segment_seconds=4,
                      formal_incremental_commit=True,
                      fast_preview_enabled=preview is not None, fast_preview_window_seconds=3,
                      fast_preview_interval_seconds=1.5, fast_preview_max_backlog_seconds=3,
                      asr_retry_enabled=False, weak_speech_rescue_enabled=False,
                      qwen_startup_draft_enabled=False, qwen_orphan_max_age_seconds=10,
                      qwen_targeted_retry_enabled=False)
        source = Replay([ROOT / p for p in args.audio], args.seconds)
        config = SessionConfig(audio_source=source, audio_source_name="benchmark",
                               asr=asr, preview_asr=preview, preview_asr_backend_name=args.preview,
                               formal_asr_device=device, preview_asr_device="cpu" if args.preview == "qwen3" else device,
                               compute_gpu_available=True, hardware_profile_label=f"{device}/{hardware.cpu_count}c",
                               summarizer=HeuristicSummarizer(), translator=PassthroughTranslator(),
                               storage=storage, forced_language=None, **preset)
        manager = SessionManager(config)
        counts = Counter()
        errors = []
        text = []
        latencies = {"formal": [], "preview": []}
        rtfs = {"formal": [], "preview": []}
        seen_timings = {"formal": None, "preview": None}
        maximum_backlog = 0.0
        audio_drops = 0
        wall_start = time.monotonic()

        async def consume():
            nonlocal maximum_backlog, audio_drops
            async for event in manager.events():
                counts[event.type] += 1
                payload = event.payload
                if event.type == "transcript_segment":
                    text.append(payload.get("text", ""))
                    latencies["formal"].append(max(0, time.monotonic() - wall_start - payload["end_time"]))
                elif event.type == "transcript_preview" and payload.get("segment"):
                    latencies["preview"].append(max(0, time.monotonic() - wall_start - payload["segment"]["end_time"]))
                elif event.type == "error":
                    errors.append({"code": payload.get("code"), "message": payload.get("message")})
                elif event.type == "session_state":
                    maximum_backlog = max(maximum_backlog, payload.get("audio_input_backlog_seconds", 0))
                    audio_drops = max(audio_drops, payload.get("audio_input_queue_drop_total", 0))
                    for lane, prefix in (("formal", "asr"), ("preview", "fast_preview")):
                        timing = (payload.get(prefix + "_last_audio_seconds"), payload.get(prefix + "_last_wall_seconds"))
                        rtf = payload.get(prefix + "_realtime_factor")
                        if timing != seen_timings[lane] and rtf is not None:
                            rtfs[lane].append(rtf)
                            seen_timings[lane] = timing

        consumer = asyncio.create_task(consume())
        await manager.start()
        try:
            deadline = time.monotonic() + args.seconds + 120
            while not manager._task.done() and time.monotonic() < deadline:
                done, _ = await asyncio.wait({manager._task}, timeout=30)
                print(json.dumps({"elapsed": round(time.monotonic() - wall_start), "formal": len(latencies['formal']),
                                  "preview": len(latencies['preview']), "backlog": manager._state.audio_input_backlog_seconds}), flush=True)
            await manager.stop()
            await consumer
        finally:
            if not consumer.done():
                consumer.cancel()
        report = {"engine": engine, "device": device, "preview": args.preview, "requested_seconds": args.seconds,
                  "wall_seconds": time.monotonic() - wall_start, "fixture_paths": args.audio,
                  "events": dict(counts), "latency_seconds": {k: percentiles(v) for k, v in latencies.items()},
                  "rtf": {k: percentiles(v) for k, v in rtfs.items()}, "max_audio_backlog_seconds": maximum_backlog,
                  "audio_queue_drops": audio_drops, "errors": errors,
                  "max_process_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  "transcript": " ".join(text)}
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({k:v for k,v in report.items() if k != 'transcript'}, ensure_ascii=False), flush=True)
        if not text or any(e["code"] in {"session_loop", "asr_unavailable", "preview_asr_unavailable"} for e in errors):
            raise SystemExit("Benchmark did not complete successfully; inspect report.")
    finally:
        asr.close()
        storage.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--preview", choices=["qwen3", "shared", "whisper-streaming"], default="qwen3")
    parser.add_argument("--audio", nargs="+", default=["data/sample_en.wav"])
    parser.add_argument("--output", default=".runtime/benchmark/apple-gpu.json")
    arguments = parser.parse_args()
    if arguments.seconds <= 0:
        parser.error("seconds must be positive")
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main(arguments))
