import math
from types import SimpleNamespace

from speechify.coach import CAMERA_CENTER, CAMERA_TILT_DEG, DEG_PER_PX, Coach, Word, filler_label
from speechify.neon import head_angles

EYES_AHEAD = (CAMERA_CENTER[0], CAMERA_CENTER[1] - CAMERA_TILT_DEG / DEG_PER_PX)


def quaternion(axis: str, degrees: float) -> SimpleNamespace:
    half = math.radians(degrees) / 2
    q = {"x": 0.0, "y": 0.0, "z": 0.0, "w": math.cos(half)}
    q[axis] = math.sin(half)
    return SimpleNamespace(**q)


def test_head_angles():
    pitch, yaw = head_angles(quaternion("x", -20))  # tilt the head down 20 degrees
    assert round(pitch) == -20 and round(yaw) == 0
    pitch, yaw = head_angles(quaternion("z", -30))  # turn right (clockwise seen from above)
    assert round(pitch) == 0 and round(yaw) == 30


def test_filler_label():
    you = Word("you", 0, 0)
    assert filler_label(Word("Um,", 0, 0), None) == "um"
    assert filler_label(Word("like,", 0, 0), None) == "like"
    assert filler_label(Word("like", 0, 0), None) is None  # "I'd like to..." isn't a filler
    assert filler_label(Word("know,", 0, 0), you) == "you know"
    assert filler_label(Word("know", 0, 0), you) is None


def started_coach():
    cues = []
    coach = Coach(lambda kind, message: cues.append(kind))
    look(coach, 0, 2)  # look at the audience before starting, for calibration
    assert coach.start(2)
    return coach, cues


def look(coach, t_from, t_to, pitch=0.0, yaw=0.0):
    """Hold the head at pitch/yaw (eyes straight ahead) from t_from to t_to."""
    t = t_from
    while t < t_to:
        coach.on_head(t, pitch, yaw)
        coach.on_gaze(t, *EYES_AHEAD, worn=True)
        coach.tick(t)
        t += 0.02


def test_looking_down_cues_after_sustain():
    coach, cues = started_coach()
    look(coach, 2, 4, pitch=-35)
    assert "look_up" not in cues  # 2 s of looking down is fine
    look(coach, 4, 5.5, pitch=-35)
    assert "look_up" in cues


def test_stuck_on_one_side_nudges_the_other_way():
    coach, cues = started_coach()
    look(coach, 2, 11.5, yaw=40)  # facing right
    assert "look_left" in cues


def test_rushing_triggers_too_fast():
    coach, cues = started_coach()
    t = 2.0
    while t < 20:  # 200 words per minute
        coach.on_word(Word("word", t, t + 0.25))
        coach.tick(t)
        t += 0.3
    assert "too_fast" in cues
    assert coach.report()["avg_wpm"] > 180


def test_filler_cooldown():
    coach, cues = started_coach()
    for t in (3.0, 3.5, 6.0):
        coach.on_word(Word("um", t, t + 0.2))
    assert cues.count("filler") == 2  # the second "um" is within the 1.5 s cooldown


def test_report_eye_contact():
    coach, cues = started_coach()
    look(coach, 2, 8)
    look(coach, 8, 10, pitch=-35)
    coach.stop(10)
    report = coach.report()
    assert 0.6 < report["eye_contact"] < 0.85  # 6 s at the audience, 2 s down
    assert len(report["down_spans"]) == 1
