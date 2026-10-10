"""Audience Q&A: after your talk, an AI audience member asks you a pointed question out loud.

The question is written by an LLM on AssemblyAI's LLM Gateway (same key as
transcription) and spoken with ElevenLabs text-to-speech. Your answer is then
coached like any other session.
"""

from __future__ import annotations

import os

import httpx

from .config import QUESTION_MODEL, VOICE_ID, VOICE_MODEL

PROMPT = """You are in the audience of the talk below. Ask the speaker one pointed, specific \
question about something they actually said - the kind that makes them think on their feet.
Reply with only the question, in one or two conversational sentences.

Transcript:
{transcript}"""


def _key(name: str) -> str:
    key = os.environ.get(name)
    if not key:
        raise RuntimeError(f"add {name} to .env")
    return key


async def write_question(transcript: str) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://llm-gateway.assemblyai.com/v1/chat/completions",
            headers={"authorization": _key("ASSEMBLYAI_API_KEY")},
            json={
                "model": QUESTION_MODEL,
                "max_tokens": 2000,
                "messages": [{"role": "user", "content": PROMPT.format(transcript=transcript)}],
            },
        )
    if response.status_code != 200:
        raise RuntimeError(f"LLM Gateway error {response.status_code}: {response.text[:300]}")
    return response.json()["choices"][0]["message"]["content"].strip()


async def speak(text: str) -> bytes:
    """Text -> MP3 audio of the audience member's voice."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}",
            headers={"xi-api-key": _key("ELEVENLABS_API_KEY")},
            json={"text": text, "model_id": VOICE_MODEL},
        )
    if response.status_code != 200:
        raise RuntimeError(f"ElevenLabs error {response.status_code}: {response.text[:300]}")
    return response.content
