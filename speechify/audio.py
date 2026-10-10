"""Laptop microphone + live transcription with AssemblyAI (Universal-3.6 Pro streaming).

Universal-3.6 Pro transcribes verbatim, so "um" and "uh" come through as words -
that's what the filler-word cue depends on. Speak an "um" in a test session and
check that it shows up in the live caption.
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl

import certifi
import numpy as np
import sounddevice as sd
from websockets.asyncio.client import connect

from .coach import Word

SAMPLE_RATE = 16000
ASSEMBLYAI_URL = (
    "wss://streaming.assemblyai.com/v3/ws"
    f"?speech_model=universal-3-6-pro&encoding=pcm_s16le&sample_rate={SAMPLE_RATE}"
)


def start_microphone(on_audio) -> sd.InputStream:
    """Call on_audio(pcm) on the event loop for every 100 ms of 16 kHz mono int16 audio."""
    loop = asyncio.get_running_loop()

    def callback(indata, frames, time_info, status):  # runs on PortAudio's thread
        loop.call_soon_threadsafe(on_audio, indata[:, 0].copy())

    mic = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                         blocksize=SAMPLE_RATE // 10, callback=callback)
    mic.start()
    return mic


def level_db(pcm: np.ndarray) -> float:
    """Loudness in dBFS: about -60 in a quiet room, -30 to -15 when speaking."""
    rms = np.sqrt(np.mean(pcm.astype(np.float32) ** 2)) / 32768
    return float(20 * np.log10(max(rms, 1e-6)))


async def transcribe(audio: asyncio.Queue, t0: float, on_turn, on_caption, status: dict) -> None:
    """Send audio chunks from the queue (None = end of session) to AssemblyAI.

    AssemblyAI re-sends the whole turn (roughly, the current sentence) about once
    a second while you speak, and marks it final after a pause. Each time, this
    calls on_turn(words, final) and on_caption(text). t0 is when the first queued
    chunk was recorded; AssemblyAI's word times are ms from the start of our audio.
    """
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key:
        status["transcription"] = "off - add ASSEMBLYAI_API_KEY to .env"
        return
    # python.org's macOS Python ships without root certificates; certifi's bundle works everywhere.
    tls = ssl.create_default_context(cafile=certifi.where())
    try:
        async with connect(ASSEMBLYAI_URL, additional_headers={"Authorization": key}, ssl=tls) as ws:
            status["transcription"] = "AssemblyAI live"

            async def send_audio():
                while (pcm := await audio.get()) is not None:
                    await ws.send(pcm.tobytes())
                await ws.send(json.dumps({"type": "Terminate"}))  # flushes the last turn

            sender = asyncio.create_task(send_audio())
            finished_turns = set()
            async for message in ws:
                msg = json.loads(message)
                if msg["type"] == "Turn" and msg["turn_order"] not in finished_turns:
                    words = [Word(w["text"], t0 + w["start"] / 1000, t0 + w["end"] / 1000) for w in msg["words"]]
                    if msg["end_of_turn"]:
                        finished_turns.add(msg["turn_order"])
                    on_turn(words, msg["end_of_turn"])
                    on_caption(msg["transcript"])
                elif msg["type"] == "Termination":
                    break
            sender.cancel()
        status["transcription"] = "AssemblyAI ready"
    except Exception as e:
        status["transcription"] = f"error: {e}"
