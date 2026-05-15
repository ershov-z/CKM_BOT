from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class RejectReason:
    button_name: str
    reply_text: str


def load_reject_reasons(path: str | Path) -> list[RejectReason]:
    reasons_path = Path(path)
    raw = json.loads(reasons_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise RuntimeError("Reject reasons file must contain a JSON array.")

    reasons: list[RejectReason] = []
    for item in raw:
        # Backward compatibility for plain string records.
        if isinstance(item, str):
            text = item.strip()
            if not text:
                continue
            reasons.append(RejectReason(button_name=text, reply_text=f"Отклонено: {text}"))
            continue

        if not isinstance(item, dict):
            continue

        button_name = str(item.get("button_name", "")).strip()
        reply_text = str(item.get("reply_text", "")).strip()
        if not button_name or not reply_text:
            continue
        reasons.append(RejectReason(button_name=button_name, reply_text=reply_text))

    if not reasons:
        raise RuntimeError("Reject reasons file must not be empty.")
    return reasons
