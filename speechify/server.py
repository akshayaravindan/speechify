"""Web server and wiring:

    glasses (neon.py) ----------------.
    mic -> AssemblyAI (audio.py) -----+--> Coach (coach.py) --cue--> DataFeel dots (haptics.py)
    or fakes of both (simulator.py) --'                       '----> dashboard (web/index.html)

The dashboard polls GET /api/state a few times a second; open it in a browser
(or curl it) to see exactly what the app sees.

After a talk, POST /api/question has an AI audience member (qa.py) ask about it
out loud; answering is just another session, started with /api/start?answer=true.
Retrying replays the same question, and the new answer's report includes the
previous try's numbers for a before/after.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

from . import audio, config, neon, qa, simulator
from .coach import Coach
from .haptics import PATTERNS, Haptics

# Set by __main__ before the server starts.
SIM: str | None = None  # None (real hardware), "all", "glasses" or "speech"
NEON_IP: str | None = None

INDEX_HTML = Path(__file__).parent / "web" / "index.html"
SESSIONS_DIR = Path("sessions")

status = {"glasses": "starting...", "mic_db": None, "transcription": "starting...", "haptics": "", "caption": ""}
events: list[dict] = []  # every cue so far; the dashboard asks for the ones it hasn't seen
session = {"audio": None, "task": None, "report": None,  # the session in progress
           # Audience Q&A. Questions are always about the last talk ("talk" is its transcript);
           # "asked" prevents repeats; "attempt" is the last answer to the current question.
           "question": None, "question_audio": None, "answering": False,
           "talk": "", "asked": [], "attempt": None}
haptics = Haptics()


def on_cue(kind: str, message: str) -> None:
    haptics.play(kind)
    role, _, color, thermal = PATTERNS[kind]
    t = round(time.monotonic() - coach.t0, 1) if coach.active else None  # None = practice tap
    events.append({"t": t, "kind": kind, "text": message, "role": role, "color": color, "thermal": thermal})


coach = Coach(on_cue)


def on_audio(pcm) -> None:  # every 100 ms from the microphone
    status["mic_db"] = round(audio.level_db(pcm))
    if session["audio"] is not None:
        session["audio"].put_nowait(pcm)


def on_caption(text: str) -> None:
    status["caption"] = text


async def tick_forever() -> None:
    while True:
        coach.tick(time.monotonic())
        await asyncio.sleep(0.1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    haptics.start()
    tasks = [asyncio.create_task(tick_forever())]
    if SIM in ("all", "glasses"):
        status["glasses"] = "SIMULATED"
        tasks.append(asyncio.create_task(simulator.fake_glasses(coach)))
    else:
        tasks.append(asyncio.create_task(neon.stream(coach, status, NEON_IP)))
    mic = None
    if SIM in ("all", "speech"):
        status["transcription"] = "SIMULATED speech"
    else:
        has_key = bool(os.environ.get("ASSEMBLYAI_API_KEY"))
        status["transcription"] = "AssemblyAI ready" if has_key else "off - add ASSEMBLYAI_API_KEY to .env"
        try:
            mic = audio.start_microphone(on_audio)
        except Exception as e:
            status["transcription"] = f"no microphone: {e}"
    yield
    for task in tasks:
        task.cancel()
    if mic:
        mic.close()
    haptics.close()


app = FastAPI(lifespan=lifespan)


@app.get("/")
def index():
    return FileResponse(INDEX_HTML)


@app.get("/api/state")
def state(since: int = 0):
    status["haptics"] = haptics.status
    return {
        "sim": SIM,
        "limits": {"fast": config.TOO_FAST_WPM, "slow": config.TOO_SLOW_WPM, "ideal": config.IDEAL_WPM,
                   "side": config.SIDE_DEG, "down": config.LOOK_DOWN_DEG, "up": config.LOOK_UP_DEG},
        "status": status,
        "dots": haptics.role_addresses(),
        "live": coach.live(time.monotonic()),
        "events": events[since:],
        "next": len(events),
    }


@app.post("/api/start")
async def start(answer: bool = False):
    """Start a session. answer=true means you're answering the audience question."""
    if coach.active:
        raise HTTPException(409, "a session is already running")
    session["answering"] = answer
    now = time.monotonic()
    calibrated = coach.start(now)  # whatever you looked at for the last 2 s = the audience
    haptics.play("start")
    if SIM in ("all", "speech"):
        session["task"] = asyncio.create_task(simulator.fake_speech(coach.on_turn, on_caption, status))
    else:
        session["audio"] = asyncio.Queue()
        session["task"] = asyncio.create_task(
            audio.transcribe(session["audio"], now, coach.on_turn, on_caption, status))
    return {"calibrated": calibrated}


@app.post("/api/stop")
async def stop():
    if not coach.active:
        raise HTTPException(409, "no session is running")
    if session["audio"] is not None:
        session["audio"].put_nowait(None)  # tell the transcriber to finish...
        session["audio"] = None
        try:
            await asyncio.wait_for(session["task"], timeout=5)  # ...and wait for the last words
        except asyncio.TimeoutError:
            pass
    else:
        session["task"].cancel()
    coach.stop(time.monotonic())
    haptics.play("stop")
    status["caption"] = ""

    report = coach.report()
    if session["answering"]:
        report["question"] = session["question"]
        report["previous_attempt"] = session["attempt"]  # None on the first try
        session["attempt"] = {k: report[k] for k in ("fillers", "avg_wpm", "eye_contact")}
    else:  # a new talk: questions from now on are about this one
        session["talk"] = " ".join(word for _, word, _ in report["transcript"])
        session["asked"] = []
    SESSIONS_DIR.mkdir(exist_ok=True)
    path = SESSIONS_DIR / time.strftime("%Y-%m-%d_%H-%M-%S.json")
    path.write_text(json.dumps(report, indent=2))
    report["saved_to"] = str(path)
    session["report"] = report
    return report


@app.get("/api/report")
def last_report():
    if session["report"] is None:
        raise HTTPException(404, "no finished session yet")
    return session["report"]


@app.post("/api/question")
async def question():
    """An AI audience member writes a new pointed question about your last talk."""
    if not session["talk"]:
        raise HTTPException(409, "give a talk first (with transcription on)")
    try:
        session["question"] = await qa.write_question(session["talk"], session["asked"])
        session["question_audio"] = await qa.speak(session["question"])
    except Exception as e:
        raise HTTPException(502, f"couldn't get a question: {e}")
    session["asked"].append(session["question"])
    session["attempt"] = None  # a new question: no earlier answer to compare with
    return {"question": session["question"]}


@app.get("/api/question.mp3")
def question_audio():
    if session["question_audio"] is None:
        raise HTTPException(404, "no question yet")
    return Response(session["question_audio"], media_type="audio/mpeg")


@app.post("/api/haptics/{kind}")
def feel(kind: str):
    """Play one cue's pattern, so the speaker can learn the haptic language."""
    if kind not in PATTERNS:
        raise HTTPException(404, f"unknown cue; try one of {list(PATTERNS)}")
    on_cue(kind, kind.replace("_", " "))
    return {"played": kind}
