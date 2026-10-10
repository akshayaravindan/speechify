"""All the coaching logic: sensor data in, cues and an end-of-session report out.

Times are seconds from time.monotonic(). Inputs:
    on_head(t, pitch, yaw)   head orientation from the Neon IMU, degrees
    on_gaze(t, x, y, worn)   gaze point in Neon scene-camera pixels
    on_turn(words, final)    the sentence being spoken so far, re-sent as the transcriber revises it
tick(t) runs the time-based rules ~10x per second. Every cue goes to
on_cue(kind, message), which the server turns into a haptic + dashboard event.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict, deque
from dataclasses import dataclass

from .config import (
    FILLER_BURST_COOLDOWN_S, FILLER_BURST_COUNT, FILLER_BURST_WINDOW_S,
    FILLER_COOLDOWN_S, IDEAL_WPM, LOOK_DOWN_DEG, LOOK_DOWN_REMIND_S, LOOK_DOWN_S, LOOK_UP_DEG,
    PACE_REMIND_S, PACE_SUSTAIN_S, PACE_WINDOW_S, SIDE_DEG, STUCK_SIDE_REMIND_S, STUCK_SIDE_S,
    TOO_FAST_WPM, TOO_SLOW_WPM,
)

# Neon scene camera: 1600x1200 px covering ~103x77 degrees. It points slightly
# down, so looking straight ahead lands ~12 degrees above the image center.
CAMERA_CENTER = (800, 600)
DEG_PER_PX = 0.064
CAMERA_TILT_DEG = 12

HARD_FILLERS = {"um", "umm", "uhm", "uh", "uhh", "er", "erm", "ah", "hmm", "mm"}
CRUTCH_WORDS = {"basically", "literally"}


@dataclass
class Word:
    text: str  # as transcribed, punctuation included ("Like,")
    start: float
    end: float
    filler: str | None = None  # set by the coach: "um", "like", "you know", ...


def filler_label(word: Word, previous: Word | None) -> str | None:
    text = word.text.lower()
    bare = text.strip(".,?!;:")
    if bare in HARD_FILLERS or bare in CRUTCH_WORDS:
        return bare
    if text == "like,":  # "like," set off by a comma is the filler, not the verb
        return "like"
    if text == "know," and previous and previous.text.lower() == "you":
        return "you know"
    return None


def wrap_degrees(angle: float) -> float:
    return (angle + 180) % 360 - 180


class Coach:
    def __init__(self, on_cue):
        self.on_cue = on_cue
        self.head = None  # latest (t, pitch, yaw)
        self.gaze = None  # smoothed (azimuth, elevation), degrees from the calibrated "audience"
        self.gaze_t = 0.0
        self.zone = None  # "center" | "left" | "right" | "down" | "up" | None (no gaze)
        self.zone_since = 0.0
        self.recent = deque(maxlen=600)  # raw (t, eye_az, eye_el, pitch, yaw) for calibration
        self.calibration = {"eye_az": 0.0, "eye_el": CAMERA_TILT_DEG, "pitch": 0.0, "yaw": None}
        self.active = False
        self._reset(0.0)

    def _reset(self, t: float) -> None:
        self.t0 = self.t_end = t
        self.words: list[Word] = []  # finished turns - the transcript
        self.partial: list[Word] = []  # the turn still being spoken (the transcriber may revise it)
        self.turn_fillers = 0  # fillers already cued in the current turn
        self.filler_times: list[float] = []  # when each filler was heard, to spot a streak
        self.cues: list[tuple[float, str, str]] = []  # (t, kind, message)
        self.zone_time: Counter[str] = Counter()  # seconds spent in each gaze zone
        self.down_spans: list[tuple[float, float]] = []
        self.timeline: list[tuple[float, int | None, str | None]] = []  # (t, wpm, zone) each second
        self.wpm: float | None = None
        self.pace = "ok"  # "ok" | "fast" | "slow"
        self.pace_seen, self.pace_seen_since = "ok", t  # latest raw reading, for the sustain timer
        self.last_cue: defaultdict[str, float] = defaultdict(lambda: -math.inf)  # kind -> last fired
        self.next_sample = t + 1

    # ---------------------------------------------------------------- session

    def start(self, t: float) -> bool:
        """Start a session. Returns whether gaze calibration succeeded."""
        calibrated = self.calibrate(t)
        self._reset(t)
        self.active = True
        return calibrated

    def stop(self, t: float) -> None:
        if self.zone == "down":
            self.down_spans.append((self.zone_since, t))
        self.active = False
        self.t_end = t

    def calibrate(self, t: float) -> bool:
        """Wherever the speaker looked for the last 2 seconds is "the audience"."""
        samples = [s for s in self.recent if s[0] > t - 2]
        if len(samples) < 20:
            return False
        with_head = [s for s in samples if s[3] is not None]
        self.calibration = {
            "eye_az": sum(s[1] for s in samples) / len(samples),
            "eye_el": sum(s[2] for s in samples) / len(samples),
            "pitch": sum(s[3] for s in with_head) / len(with_head) if with_head else 0.0,
            "yaw": with_head[-1][4] if with_head else None,  # latest heading (averaging breaks at +-180)
        }
        self.gaze = None
        return True

    # ---------------------------------------------------------------- inputs

    def on_head(self, t: float, pitch: float, yaw: float) -> None:
        self.head = (t, pitch, yaw)
        if self.calibration["yaw"] is None:
            self.calibration["yaw"] = yaw  # until calibrated, the first heading counts as "forward"

    def on_gaze(self, t: float, x: float, y: float, worn: bool) -> None:
        if not worn:
            self._set_zone(t, None)
            return
        eye_az = (x - CAMERA_CENTER[0]) * DEG_PER_PX
        eye_el = (CAMERA_CENTER[1] - y) * DEG_PER_PX
        pitch = yaw = None
        if self.head and t - self.head[0] < 0.5:
            _, pitch, yaw = self.head
        self.recent.append((t, eye_az, eye_el, pitch, yaw))

        # Where you look = where your head points + where your eyes point within your head.
        cal = self.calibration
        az = eye_az - cal["eye_az"]
        el = eye_el - cal["eye_el"]
        if pitch is not None and cal["yaw"] is not None:
            az += wrap_degrees(yaw - cal["yaw"])
            el += pitch - cal["pitch"]

        if self.gaze is None or t - self.gaze_t > 0.5:
            self.gaze = (az, el)
        else:  # light smoothing: raw gaze jitters sample to sample
            self.gaze = (self.gaze[0] + 0.1 * (az - self.gaze[0]), self.gaze[1] + 0.1 * (el - self.gaze[1]))
        az, el = self.gaze
        if el < LOOK_DOWN_DEG:
            zone = "down"
        elif el > LOOK_UP_DEG:
            zone = "up"
        elif az < -SIDE_DEG:
            zone = "left"
        elif az > SIDE_DEG:
            zone = "right"
        else:
            zone = "center"
        self._set_zone(t, zone)

    def _set_zone(self, t: float, zone: str | None) -> None:
        if self.active and self.zone:
            self.zone_time[self.zone] += min(t - self.gaze_t, 0.1)  # a dropout doesn't count
        self.gaze_t = t
        if zone != self.zone:
            if self.active and self.zone == "down" and t - self.zone_since > 1:
                self.down_spans.append((self.zone_since, t))
            self.zone, self.zone_since = zone, t

    def on_turn(self, words: list[Word], final: bool) -> None:
        """The words of the current turn so far. The transcriber re-sends the whole
        turn about once a second, but only marks it final after a pause - so react
        to fillers right away, and add words to the transcript once final."""
        if not self.active:
            return
        for i, word in enumerate(words):
            previous = words[i - 1] if i else (self.words[-1] if self.words else None)
            word.filler = filler_label(word, previous)
        fillers = [w for w in words if w.filler]
        new = fillers[self.turn_fillers:]  # fillers we haven't reacted to yet
        self.filler_times += [w.end for w in new]
        if new:
            t = new[-1].end
            streak = sum(1 for heard in self.filler_times if heard > t - FILLER_BURST_WINDOW_S)
            if streak >= FILLER_BURST_COUNT and t - self.last_cue["filler_burst"] >= FILLER_BURST_COOLDOWN_S:
                self.last_cue["filler"] = t  # the strong buzz replaces this filler's tick
                self._cue(t, "filler_burst", f"{streak} fillers in {FILLER_BURST_WINDOW_S}s - pause and breathe")
            elif t - self.last_cue["filler"] >= FILLER_COOLDOWN_S:
                self._cue(t, "filler", f'Filler: "{new[-1].filler}"')
        # Track the latest count even when it drops (a revision can remove a filler),
        # so the next filler still registers as new.
        self.turn_fillers = len(fillers)
        if final:
            self.words.extend(words)
            self.partial = []
            self.turn_fillers = 0
        else:
            self.partial = words

    # ---------------------------------------------------------------- rules

    def current_zone(self, now: float) -> str | None:
        return self.zone if now - self.gaze_t < 1 else None  # no gaze for a second = unknown

    def current_wpm(self, now: float) -> float | None:
        recent = [w for w in self.words[-100:] + self.partial if w.end > now - PACE_WINDOW_S and not w.filler]
        if len(recent) < 5 or now - recent[-1].end > 3:
            return None  # not enough speech yet, or pausing (a pause isn't "slow")
        span = recent[-1].end - recent[0].start
        return len(recent) / span * 60 if span > 3 else None

    def tick(self, now: float) -> None:
        if not self.active:
            return
        zone = self.current_zone(now)
        held = now - self.zone_since

        if zone == "down" and held >= LOOK_DOWN_S and now - self.last_cue["look_up"] >= LOOK_DOWN_REMIND_S:
            self._cue(now, "look_up", f"Looking down for {held:.0f}s - eyes up to your audience")

        if zone in ("left", "right") and held >= STUCK_SIDE_S and now - self.last_cue["side"] >= STUCK_SIDE_REMIND_S:
            other = "right" if zone == "left" else "left"
            self.last_cue["side"] = now
            self._cue(now, f"look_{other}", f"Facing {zone} for {held:.0f}s - include the {other} side")

        # Pace must stay out of range for PACE_SUSTAIN_S before the dot reacts.
        self.wpm = self.current_wpm(now)
        if self.wpm is None or TOO_SLOW_WPM <= self.wpm <= TOO_FAST_WPM:
            pace = "ok"
        else:
            pace = "fast" if self.wpm > TOO_FAST_WPM else "slow"
        if pace != self.pace_seen:
            self.pace_seen, self.pace_seen_since = pace, now
        sustained = now - self.pace_seen_since >= PACE_SUSTAIN_S
        reminder = pace != "ok" and now - self.last_cue["pace"] >= PACE_REMIND_S
        if sustained and (pace != self.pace or reminder):
            self.pace = pace
            self.last_cue["pace"] = now
            wpm = f"{self.wpm:.0f} WPM" if self.wpm else "paused"
            if pace == "fast":
                self._cue(now, "too_fast", f"Rushing ({wpm}) - slow down, breathe")
            elif pace == "slow":
                self._cue(now, "too_slow", f"Dragging ({wpm}) - pick up the pace")
            else:
                self._cue(now, "pace_ok", f"Pace back on track ({wpm})")

        if now >= self.next_sample:
            self.next_sample = now + 1
            self.timeline.append((round(now - self.t0, 1), round(self.wpm) if self.wpm else None, zone))

    def _cue(self, t: float, kind: str, message: str) -> None:
        self.last_cue[kind] = t
        self.cues.append((t, kind, message))
        self.on_cue(kind, message)

    # ---------------------------------------------------------------- outputs

    def live(self, now: float) -> dict:
        """What the dashboard shows while you speak."""
        zone = self.current_zone(now)
        return {
            "active": self.active,
            "elapsed": round((now if self.active else self.t_end) - self.t0),
            "wpm": round(self.wpm) if self.active and self.wpm else None,
            "pace": self.pace,
            "fillers": sum(1 for w in self.words if w.filler) + self.turn_fillers,
            "zone": zone,
            "gaze": [round(self.gaze[0], 1), round(self.gaze[1], 1)] if zone and self.gaze else None,
        }

    def report(self) -> dict:
        duration = self.t_end - self.t0
        minutes = max(duration / 60, 1 / 60)
        spoken = [w for w in self.words if not w.filler]
        fillers = [w.filler for w in self.words if w.filler]
        tracked = sum(self.zone_time.values())
        gaze_tracked = tracked > 3
        shares = {z: round(self.zone_time[z] / tracked, 3) for z in ("left", "center", "right", "down", "up")} if gaze_tracked else None
        speaking_span = spoken[-1].end - spoken[0].start if len(spoken) > 1 else 0
        rel = lambda t: round(t - self.t0, 1)  # noqa: E731 - seconds from session start
        report = {
            "duration_s": round(duration),
            "words": len(spoken),
            "avg_wpm": round(len(spoken) / speaking_span * 60) if speaking_span > 5 else None,
            "ideal_wpm": IDEAL_WPM,
            "pace_limits": [TOO_SLOW_WPM, TOO_FAST_WPM],
            "fillers": len(fillers),
            "fillers_per_min": round(len(fillers) / minutes, 1),
            "filler_counts": Counter(fillers).most_common(),
            "gaze_tracked": gaze_tracked,
            "gaze_share": shares,
            "eye_contact": round(shares["left"] + shares["center"] + shares["right"], 3) if gaze_tracked else None,
            "down_spans": [[rel(a), rel(b)] for a, b in self.down_spans],
            "timeline": self.timeline,
            "cues": [[rel(t), kind, message] for t, kind, message in self.cues],
            "transcript": [[rel(w.start), w.text, bool(w.filler)] for w in self.words],
        }
        report["insights"] = insights(report)
        return report


def insights(r: dict) -> list[str]:
    """The report in plain sentences."""
    out = []
    if r["avg_wpm"]:
        low, high = r["ideal_wpm"]
        verdict = "right on target" if low <= r["avg_wpm"] <= high else "too fast" if r["avg_wpm"] > high else "a bit slow"
        out.append(f"You averaged {r['avg_wpm']} words per minute - {verdict} (aim for {low}-{high}).")
    if r["words"]:
        if r["fillers"]:
            top = ", ".join(f'"{word}" x{n}' for word, n in r["filler_counts"][:3])
            out.append(f"{r['fillers']} filler words, {r['fillers_per_min']} per minute ({top}). Under 2 per minute is the goal.")
        else:
            out.append("No filler words at all.")
    if r["gaze_tracked"]:
        line = f"You were looking at your audience {r['eye_contact'] * 100:.0f}% of the time."
        if r["down_spans"]:
            longest = max(b - a for a, b in r["down_spans"])
            times = "once" if len(r["down_spans"]) == 1 else f"{len(r['down_spans'])} times"
            line += f" You looked down {times}; the longest was {longest:.0f}s."
        left, right = r["gaze_share"]["left"], r["gaze_share"]["right"]
        if max(left, right) > 0.1 and min(left, right) < 0.03:
            line += f" The {'left' if left < right else 'right'} side of the room barely got a look."
        out.append(line)
    return out
