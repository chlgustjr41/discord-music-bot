"""Closed action vocabulary for voice commands, plus the validator.

This is the security boundary. The model's output is UNTRUSTED input: the
JSON schema constrains it at decode time, but that guarantee is re-checked
here regardless. A schema the model should obey is not the same as one it did.

There is deliberately no verb for deleting a playlist, history, or a session.
That is the guarantee — not a prompt instruction the model might ignore.
`clear_queue` is the only destructive action and empties only the queue.
"""

from dataclasses import dataclass

MAX_ACTIONS = 5
MAX_SKIP = 10
PLACEMENTS = ("now", "next", "end")
LOOP_MODES = ("off", "track", "queue")

_VERBS = (
    "play", "playlist", "skip", "pause", "resume",
    "volume", "shuffle", "clear_queue", "loop",
)
_NEEDS_QUERY = ("play",)
_NEEDS_NAME = ("playlist",)


@dataclass(frozen=True)
class Action:
    action: str
    query: str = ""
    name: str = ""
    placement: str = "now"
    count: int = 1
    level: int | None = None
    delta: int | None = None
    mode: str = "off"


ACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["actions"],
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "action", "query", "name", "placement",
                    "count", "level", "delta", "mode",
                ],
                "properties": {
                    "action": {"type": "string", "enum": list(_VERBS)},
                    "query": {"type": "string"},
                    "name": {"type": "string"},
                    "placement": {"type": "string", "enum": list(PLACEMENTS)},
                    "count": {"type": "integer"},
                    # Nullable so the model can express "no absolute level" /
                    # "no relative delta" — validate_actions reads which of the
                    # two is None to tell absolute volume from relative.
                    # Strict mode has no optional fields, so optionality must
                    # be a union with null.
                    "level": {"type": ["integer", "null"]},
                    "delta": {"type": ["integer", "null"]},
                    "mode": {"type": "string", "enum": list(LOOP_MODES)},
                },
            },
        }
    },
}


def _clamp(value, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def validate_actions(raw) -> list[Action]:
    """Drop anything not in the vocabulary; clamp every number. Never raises."""
    if not isinstance(raw, list):
        return []
    out: list[Action] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        verb = entry.get("action")
        if verb not in _VERBS:
            continue
        query = str(entry.get("query") or "").strip()
        name = str(entry.get("name") or "").strip()
        if verb in _NEEDS_QUERY and not query:
            continue
        if verb in _NEEDS_NAME and not name:
            continue
        placement = entry.get("placement")
        mode = entry.get("mode")
        level = entry.get("level")
        delta = entry.get("delta")
        out.append(Action(
            action=verb,
            query=query,
            name=name,
            placement=placement if placement in PLACEMENTS else "now",
            count=_clamp(entry.get("count", 1), 1, MAX_SKIP, 1),
            level=None if level is None else _clamp(level, 0, 100, 80),
            delta=None if delta is None else _clamp(delta, -100, 100, 0),
            mode=mode if mode in LOOP_MODES else "off",
        ))
        if len(out) == MAX_ACTIONS:
            break
    return out
