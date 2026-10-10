"""Fake glasses and a fake speaker, for building without the hardware
(there are only two Neons at the venue). The dashboard labels these SIMULATED.

Within each session the fake speaker rushes from 18-38s and drags from 48-66s,
and the fake glasses read notes from 12-17s and stare right from 38-49s, so
every cue fires at a predictable time.
"""

from __future__ import annotations

import asyncio
import random
import time

from .coach import CAMERA_CENTER, CAMERA_TILT_DEG, DEG_PER_PX, Word

TALK = (
    "Every one of us has watched a great idea die on stage. Not because the idea was bad, but because the "
    "speaker read their slides, rushed the best part, and filled every silence with noise. Speechify is a "
    "coach you wear. The glasses see when you are reading your notes instead of talking to people. The "
    "microphone hears your pace and every filler word. And instead of a screen you cannot look at, you "
    "feel the feedback. A tick on your wrist for a filler word. A warm touch when you rush. A cool one "
    "when you drag. A nudge on the left when you have forgotten half the room. Afterwards you get the "
    "full picture: your pace over time, where you looked, and the moments that cost you. Public speaking "
    "is a skill, and skills need feedback while you are doing them, not a week later. Thank you."
)


async def fake_glasses(coach) -> None:
    head, target, next_glance = [0.0, 0.0], (0.0, 0.0), 0.0
    while True:
        now = time.monotonic()
        t = (now - coach.t0) % 120 if coach.active else -1
        if t < 0:
            target = (0.0, 0.0)  # before a session: face the audience, so calibration is clean
        elif 12 <= t < 17:
            target = (0.0, -30.0)  # reading notes
        elif 38 <= t < 49:
            target = (35.0, 0.0)  # stuck on the right side of the room
        elif now >= next_glance:  # otherwise scan around the audience
            target = (random.choice([-32.0, -10.0, 0.0, 0.0, 10.0, 32.0]), random.uniform(-4, 4))
            next_glance = now + random.uniform(1.5, 3.5)
        head[0] += 0.1 * (target[0] - head[0])  # the head turns smoothly; the eyes stay centered
        head[1] += 0.1 * (target[1] - head[1])
        coach.on_head(now, pitch=head[1], yaw=90 + head[0])
        eyes_ahead_y = CAMERA_CENTER[1] - CAMERA_TILT_DEG / DEG_PER_PX
        coach.on_gaze(now, CAMERA_CENTER[0] + random.gauss(0, 3), eyes_ahead_y + random.gauss(0, 3), worn=True)
        await asyncio.sleep(1 / 60)


async def fake_speech(on_turn, on_caption, status: dict) -> None:
    """Speak TALK once, word by word (each word its own finished turn), with fillers and changing pace."""
    rng = random.Random(4)
    start = time.monotonic()
    line: list[str] = []
    for token in TALK.split():
        if rng.random() < 0.07:
            token = rng.choice(["um", "uh", "like,"]) + " " + token
        for text in token.split():
            t = time.monotonic() - start
            seconds = 60 / (240 if 18 <= t < 38 else 75 if 48 <= t < 66 else 145)
            now = time.monotonic()
            on_turn([Word(text, now, now + 0.8 * seconds)], True)
            line = line[-11:] + [text]
            on_caption(" ".join(line))
            status["mic_db"] = round(-25 + rng.uniform(-3, 3))
            await asyncio.sleep(seconds)
        if token.endswith("."):
            status["mic_db"] = -60
            await asyncio.sleep(0.6)
    on_caption("")
