# Speechify

A speech coach you wear: eye-tracking glasses and a microphone watch how you deliver a talk, and you *feel* corrections on your skin while you speak - then get the full breakdown afterwards.

You can't read a screen while you're presenting, so the feedback is haptic. Each mistake has its own feel:

| You're... | Detected by | You feel | Dot |
|---|---|---|---|
| Saying "um", "uh", "like," | AssemblyAI live transcript | one crisp tick | main |
| Reading notes / looking down > 2.5 s | Neon gaze + head pitch | double-double tap | main |
| Facing one side of the room > 8 s | Neon gaze + head yaw | nudge on the *other* side | left / right |
| Rushing (> 170 WPM) | transcript timing | fading wave + **cooling** | pace |
| Dragging (< 105 WPM) | transcript timing | swelling wave + **warming** | pace |

The dashboard's **Learn the haptic language** buttons play each pattern so you can learn them before you speak. After a session you get your pace over time, eye contact, where you looked, every filler word in the transcript, and every cue you felt.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # add your ASSEMBLYAI_API_KEY

python -m speechify --sim all   # no hardware: fake glasses + fake speaker
python -m speechify --sim glasses   # real mic + transcription, fake glasses
python -m speechify             # everything real
```

The dashboard opens at http://localhost:8000. Press **Start**, look at your audience during the 3-second countdown (that calibrates "straight ahead"), and talk. Press **Stop** for the report. Anything simulated is labeled SIMULATED on the dashboard.

### Hardware checklist

- **Neon glasses**: plug the glasses into the Companion phone, open the Neon Companion app, and put the phone on the same Wi-Fi as the laptop. If it isn't found automatically (venue Wi-Fi often blocks discovery), run with `--neon-ip <phone IP>` (shown in the app) or set `NEON_IP` in `.env`. A phone hotspot shared by both devices also works.
- **DataFeel dots**: connect the El Jefe controller to the laptop over USB-C. 1-4 dots are detected automatically; with 4, the chain order is main, pace, left, right (edit `LAYOUTS` in `haptics.py` to change). With no dots plugged in, cues still show on the dashboard.
- **Microphone**: the laptop mic. On macOS, allow microphone access for your terminal (System Settings > Privacy & Security > Microphone); the dashboard's caption bar stays empty without it.
- **Filler words**: AssemblyAI's Universal-3.6 Pro streaming model transcribes verbatim. Say "um" in a test run and check it appears in the live caption.

## How it works

```
glasses (neon.py) ----------------.
mic -> AssemblyAI (audio.py) -----+--> Coach (coach.py) --cue--> DataFeel dots (haptics.py)
or fakes of both (simulator.py) --'                       '----> dashboard (web/index.html)
```

| File | What's in it |
|---|---|
| `speechify/coach.py` | All coaching logic: gaze angles, filler detection, the rules that fire cues, the report |
| `speechify/config.py` | Every threshold (WPM limits, gaze angles, timings) - tune here |
| `speechify/neon.py` | Neon real-time API: gaze + IMU head orientation, auto-reconnect |
| `speechify/audio.py` | Laptop mic + AssemblyAI streaming websocket |
| `speechify/haptics.py` | Cue -> vibration/LED/thermal patterns, DataFeel worker thread |
| `speechify/simulator.py` | Fake glasses and fake speaker with scripted mistakes |
| `speechify/server.py` | FastAPI server and wiring |
| `speechify/web/index.html` | The dashboard (plain HTML/JS, no build step) |

Where you're looking = where your head points (IMU) + where your eyes point within your head (gaze pixel converted to degrees), measured relative to the calibration at Start.

## Debugging

- `curl localhost:8000/api/state` returns exactly what the dashboard shows: device status, live metrics and cue events.
- `curl -X POST localhost:8000/api/haptics/filler` plays one pattern (also `look_up`, `look_left`, `look_right`, `too_fast`, `too_slow`).
- Every finished session is saved to `sessions/<timestamp>.json`.
- `python -m pytest` runs the coach tests (gaze math, filler detection, cue timing).

## Credits

Built during INTERFACE (SF Tech Week 2026). Uses [pupil-labs-realtime-api](https://github.com/pupil-labs/pl-realtime-api) (Neon), the [DataFeel devkit SDK](https://github.com/DataFeel/devkit-samples) (dots), [AssemblyAI](https://www.assemblyai.com/docs/streaming) streaming speech-to-text, FastAPI, uvicorn, sounddevice, numpy and websockets.
