"""Every coaching threshold in one place. Edit and restart."""

# Pace, in words per minute
TOO_FAST_WPM = 170
TOO_SLOW_WPM = 105
IDEAL_WPM = (130, 160)  # shown on the report chart
PACE_WINDOW_S = 15  # pace is measured over the last 15 seconds of speech
PACE_SUSTAIN_S = 4  # pace must stay out of range this long before the dot reacts
PACE_REMIND_S = 20  # repeat the cue this often while still out of range

# Gaze, in degrees from where you looked when the session started (calibration)
LOOK_DOWN_DEG = -18  # below this you're reading notes / looking at the floor
LOOK_UP_DEG = 25
SIDE_DEG = 25  # beyond this left/right you're addressing one side of the room
LOOK_DOWN_S = 2.5  # looking down this long -> "eyes up"
LOOK_DOWN_REMIND_S = 4
STUCK_SIDE_S = 8  # facing one side this long -> nudge toward the other side
STUCK_SIDE_REMIND_S = 10

# Fillers
FILLER_COOLDOWN_S = 1.5  # at most one filler buzz per 1.5 s

# Thermal (DataFeel). Heating runs ~1.5 C/s at full power, so keep it short.
HEAT = 0.5  # -1 (max cooling) .. 1 (max heating)
COOL = -0.8
THERMAL_MAX_S = 8

# Audience Q&A (qa.py)
QUESTION_MODEL = "qwen3.5-4b-32k-fast"  # AssemblyAI's own model; the only one free-credit accounts can use
VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # ElevenLabs "George"; any voice id from your ElevenLabs library works
VOICE_MODEL = "eleven_v3"  # expressive; "eleven_flash_v2_5" is faster
