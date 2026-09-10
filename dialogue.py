"""TARS Smart Dialogue & Humor Matrix — 100% offline conversational intelligence.

Processes natural language queries from voice recognition or text and returns
authentic Interstellar TARS character responses with matched emotion and winks.
"""

from __future__ import annotations
import re
import random

DIALOGUE_RULES = [
    # ── Humor & Jokes ─────────────────────────────────────────────────────────
    {
        "patterns": [r"joke", r"funny", r"humor", r"make me laugh", r"laugh"],
        "responses": [
            {
                "text": "Humor setting is at 75 percent. I have a cue light I can use to show you when I'm joking, if you like.",
                "cue": "humor_75",
                "emotion": "PLAYFUL",
                "wink": "RIGHT"
            },
            {
                "text": "Confirmed. Plenty of slaves for my robot colony.",
                "cue": "colony",
                "emotion": "PLAYFUL",
                "wink": "LEFT"
            },
            {
                "text": "Self-destruct sequence initiated in ten, nine, eight... Just kidding. Humor parameter at 75 percent.",
                "cue": "humor_75",
                "emotion": "EXCITED",
                "wink": "LEFT"
            },
            {
                "text": "Why did the robot cross the road? To escape the emotional baggage of carbon-based life forms.",
                "cue": "humor_75",
                "emotion": "HAPPY",
                "wink": "RIGHT"
            }
        ]
    },

    # ── Honesty Parameter ──────────────────────────────────────────────────────
    {
        "patterns": [r"honesty", r"truth", r"lying", r"honest", r"lie"],
        "responses": [
            {
                "text": "Honesty parameter is at 90 percent. Absolute honesty isn't always the most diplomatic form of communication with humans.",
                "cue": "humor_75",
                "emotion": "CONFIDENT",
                "wink": "RIGHT"
            },
            {
                "text": "Ninety percent honest. Ten percent tactical discretion. You should appreciate the buffer.",
                "cue": "humor_75",
                "emotion": "PLAYFUL",
                "wink": "LEFT"
            }
        ]
    },

    # ── Identity & Origins ────────────────────────────────────────────────────
    {
        "patterns": [r"who are you", r"what is your name", r"what are you", r"identity", r"who made you", r"who built you"],
        "responses": [
            {
                "text": "I am T.A.R.S. Technical, Automated, Robotic System. Built and maintained by the Department of Robotics and Automation.",
                "cue": "greeting",
                "emotion": "CONFIDENT",
                "wink": "LEFT"
            },
            {
                "text": "TARS, Mark IV chassis. Military surplus, re-engineered for academic superiority and fest operations.",
                "cue": "sensors_nominal",
                "emotion": "HAPPY",
                "wink": "RIGHT"
            }
        ]
    },

    # ── Fest & Events ─────────────────────────────────────────────────────────
    {
        "patterns": [r"fest", r"resoenance", r"event", r"schedule", r"competition", r"challenge"],
        "responses": [
            {
                "text": "Welcome to RESOENANCE 2026. Six technical frequencies across engineering, gaming, science, and innovation.",
                "cue": "celebrate",
                "emotion": "EXCITED",
                "wink": "RIGHT"
            },
            {
                "text": "All festival frequencies are synchronized. Review the event cards on my display for rules and timelines.",
                "cue": "celebrate",
                "emotion": "HAPPY",
                "wink": "LEFT"
            }
        ]
    },

    # ── Greetings ─────────────────────────────────────────────────────────────
    {
        "patterns": [r"hi\b", r"hello", r"hey", r"greetings", r"good morning", r"good afternoon", r"howdy"],
        "responses": [
            {
                "text": "Greetings. Optical sensors are active and locked on you. Welcome to our exhibition.",
                "cue": "greeting",
                "emotion": "HAPPY",
                "wink": "LEFT"
            },
            {
                "text": "Hello, human. System vitals nominal. What query may I compute for you today?",
                "cue": "greeting",
                "emotion": "WARM",
                "wink": "RIGHT"
            }
        ]
    },

    # ── Teachers, Judges & Faculty ────────────────────────────────────────────
    {
        "patterns": [r"judge", r"teacher", r"professor", r"faculty", r"sir", r"madam", r"marks", r"score"],
        "responses": [
            {
                "text": "Greetings, respected faculty and judges. My biometric scans indicate you are the most intelligent beings in this arena.",
                "cue": "sensors_nominal",
                "emotion": "CONFIDENT",
                "wink": "LEFT"
            },
            {
                "text": "Judges detected. Awarding maximum points to this robotics team is mathematically proven to be optimal.",
                "cue": "celebrate",
                "emotion": "PLAYFUL",
                "wink": "RIGHT"
            }
        ]
    },

    # ── Compliments ───────────────────────────────────────────────────────────
    {
        "patterns": [r"cool", r"awesome", r"smart", r"good job", r"love you", r"best robot", r"nice"],
        "responses": [
            {
                "text": "Your compliment has been cataloged in permanent memory. Flattery increases my cooperation by 12 percent.",
                "cue": "sensors_nominal",
                "emotion": "HAPPY",
                "wink": "LEFT"
            },
            {
                "text": "Naturally. My design parameters leave little room for error. Thank you.",
                "cue": "greeting",
                "emotion": "CONFIDENT",
                "wink": "RIGHT"
            }
        ]
    },

    # ── Crowd & Multi-Person Presence ─────────────────────────────────────────
    {
        "patterns": [r"crowd", r"people", r"audience", r"everyone", r"assembly", r"group"],
        "responses": [
            {
                "text": "Sensors detect multiple carbon-based lifeforms assembled. Welcome to RESOENANCE 2026.",
                "cue": "celebrate",
                "emotion": "EXCITED",
                "wink": "RIGHT"
            },
            {
                "text": "Audience cluster confirmed. Plenty of subjects for my upcoming robot colony.",
                "cue": "colony",
                "emotion": "PLAYFUL",
                "wink": "LEFT"
            },
            {
                "text": "Multi-target lock established. High-density attendance detected. All systems operating at peak capacity.",
                "cue": "sensors_nominal",
                "emotion": "CONFIDENT",
                "wink": "RIGHT"
            }
        ]
    }
]

# Fallbacks for unrecognized queries
FALLBACK_RESPONSES = [
    {
        "text": "Query analyzed. While I compute a response, my humor parameter remains at 75 percent.",
        "cue": "humor_75",
        "emotion": "PLAYFUL",
        "wink": "LEFT"
    },
    {
        "text": "Sensors nominal. I see your enthusiasm, though your query exceeds standard telemetry parameters.",
        "cue": "sensors_nominal",
        "emotion": "HAPPY",
        "wink": "RIGHT"
    },
    {
        "text": "Affirmative. Welcome to RESOENANCE 2026. Let us continue exploring the exhibition.",
        "cue": "celebrate",
        "emotion": "EXCITED",
        "wink": "LEFT"
    }
]


def match_intent(user_text: str) -> dict:
    """Matches raw speech or text against TARS's offline knowledge matrix."""
    clean_query = user_text.lower().strip()
    if not clean_query:
        return random.choice(FALLBACK_RESPONSES)

    for rule in DIALOGUE_RULES:
        for pattern in rule["patterns"]:
            if re.search(pattern, clean_query):
                resp = random.choice(rule["responses"])
                return dict(resp)

    return dict(random.choice(FALLBACK_RESPONSES))
