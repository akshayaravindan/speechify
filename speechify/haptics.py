"""DataFeel dots: each coaching cue has its own feel - rhythm, temperature and position.

All dots share one USB serial bus and the DataFeel SDK isn't thread-safe, so
every device call happens on a single worker thread fed by a queue. With no
dots plugged in, cues still show on the dashboard; nothing else changes.
"""

from __future__ import annotations

import queue
import threading
import time

from .config import COOL, HEAT, THERMAL_MAX_S


def pause(seconds: float) -> int:
    """A silent gap inside a vibration sequence (DataFeel's encoding)."""
    return 0x80 + round(seconds * 100)


# Vibration ids come from the DataFeel waveform library (datafeel.device.VibrationWaveforms):
# 4 sharp click, 10 double click, 14 strong buzz, 24 sharp tick, 70 long fade-out,
# 74 short fade-out, 86 short swell, 90 medium swell.
# cue -> (dot role, vibration sequence, LED color, thermal: None = unchanged, 0 = off, else -1..1)
PATTERNS = {
    "filler":     ("main",  [4],                          (255, 130, 0), None),
    "filler_burst": ("main", [14],                        (255, 0, 160), None),
    "look_up":    ("main",  [10, pause(0.2), 10],         (0, 90, 255),  None),
    "look_left":  ("left",  [86, 24, pause(0.08), 24],    (160, 0, 255), None),
    "look_right": ("right", [86, 24, pause(0.08), 24],    (160, 0, 255), None),
    "too_fast":   ("pace",  [70],                         (255, 50, 0),  HEAT),
    "too_slow":   ("pace",  [90, 4],                      (0, 200, 255), COOL),
    "pace_ok":    ("pace",  [],                           None,          0),
    "start":      ("all",   [86],                         (0, 255, 90),  None),
    "stop":       ("all",   [74],                         None,          0),
}

# Which dot (by position in the chain) plays each role, for 1-4 connected dots.
LAYOUTS = {
    1: {"main": 0, "pace": 0, "left": 0, "right": 0},
    2: {"main": 0, "pace": 0, "left": 0, "right": 1},
    3: {"main": 0, "pace": 0, "left": 1, "right": 2},
    4: {"main": 0, "pace": 1, "left": 2, "right": 3},
}


class Haptics:
    def __init__(self) -> None:
        self.status = "looking for DataFeel dots..."
        self.roles: dict = {}  # role -> Dot
        self._dots: list = []
        self._queue: queue.Queue = queue.Queue()
        self._thermal_off_at: dict = {}  # Dot -> time to switch its thermal off (safety)

    def start(self) -> None:
        threading.Thread(target=self._run, name="datafeel", daemon=True).start()

    def play(self, kind: str) -> None:
        self._queue.put(kind)

    def close(self) -> None:
        self._queue.put(None)

    def _run(self) -> None:
        self._connect()
        while True:
            try:
                kind = self._queue.get(timeout=0.5)
            except queue.Empty:
                kind = ""
            try:
                if kind is None:  # shutting down
                    for dot in self._dots:
                        dot.stop_vibration()
                        dot.disable_all_thermal()
                        dot.set_led_off()
                    return
                self._expire_thermal()
                if kind:
                    self._play(kind)
            except Exception as e:  # e.g. a cable pulled mid-session; keep the app running
                self.status = f"DataFeel error: {e}"

    def _connect(self) -> None:
        try:
            from datafeel.device import discover_devices

            self._dots = sorted(discover_devices(4), key=lambda dot: dot.id)
        except Exception as e:
            self.status = f"no DataFeel ({e}) - cues show on screen only"
            return
        if not self._dots:
            self.status = "no DataFeel dots on USB - cues show on screen only"
            return
        layout = LAYOUTS[len(self._dots)]
        self.roles = {role: self._dots[i] for role, i in layout.items()}
        for dot in self._dots:
            dot.disable_all_thermal()
            dot.set_led_off()
        self.status = f"{len(self._dots)} DataFeel dot(s) connected"

    def _play(self, kind: str) -> None:
        role, vibration, color, thermal = PATTERNS[kind]
        dots = self._dots if role == "all" else [self.roles[role]] if role in self.roles else []
        for dot in dots:
            if vibration:
                dot.play_vibration_sequence(vibration)
            if color:
                dot.set_led(*color)
            else:
                dot.set_led_off()
            if thermal == 0:
                dot.disable_all_thermal()
                self._thermal_off_at.pop(dot, None)
            elif thermal:
                dot.activate_thermal_intensity_control(thermal)
                self._thermal_off_at[dot] = time.monotonic() + THERMAL_MAX_S

    def _expire_thermal(self) -> None:
        now = time.monotonic()
        for dot, off_at in list(self._thermal_off_at.items()):
            if now >= off_at:
                dot.disable_all_thermal()
                del self._thermal_off_at[dot]

    def role_addresses(self) -> dict:
        """role -> dot address, for the dashboard."""
        return {role: dot.id for role, dot in self.roles.items()}
