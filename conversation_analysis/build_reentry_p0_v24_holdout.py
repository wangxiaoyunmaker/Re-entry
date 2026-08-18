"""Build the prospective v2.4 P0 holdout selected before v2.4 prompt authoring."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "outputs/reentry_p0_recall_20260817/p0_scan_chunks.jsonl"
OUT_DIR = ROOT / "outputs/reentry_p0_recall_20260817/p0_v24_current_theory_holdout"
HOLDOUT_PREFIX = "V24H"
PROMPT_VERSION = "v2.4"

SELECTIONS = [
    ("failure", "Cn2002215-", "conversation_0004", "E000019"),
    ("failure", "13162828717", "conversation_0001", "E000049"),
    ("failure", "Pineraindew", "conversation_0001", "E000227"),
    ("failure", "yaoshi1019", "conversation_0051", "E000155"),
    ("failure", "_微信待确认_Lumno_志愿者", "conversation_0117", "E000044"),
    ("failure", "15077877013", "conversation_0049", "E000613"),
    ("failure", "xmf5525436", "conversation_0001", "E001869"),
    ("failure", "zyf2492313716", "conversation_0003", "E000329"),
    ("failure", "wwen_713", "conversation_0003", "E000160"),
    ("failure", "xxy050628", "conversation_0001", "E000227"),
    ("governance", "srxh1683128236", "conversation_0003", "E000030"),
    ("governance", "15077877013", "conversation_0045", "E000708"),
    ("governance", "fyjjz666", "conversation_0001", "E000218"),
    ("governance", "13627629387", "conversation_0009", "E000006"),
    ("governance", "13162828717", "conversation_0004", "E000172"),
    ("governance", "CorneliaStreet233", "conversation_0010", "E000014"),
    ("governance", "zyf2492313716", "conversation_0003", "E000221"),
    ("governance", "_微信待确认_Cyber-Agent", "conversation_0001", "E000094"),
    ("ordinary", "15077877013", "conversation_0036", "E000193"),
    ("ordinary", "13162828717", "conversation_0002", "E000140"),
    ("ordinary", "fyjjz666", "conversation_0001", "E000151"),
    ("ordinary", "_微信待确认_PhotoMind_被试实验", "conversation_0001", "E000043"),
    ("ordinary", "xmf5525436", "conversation_0001", "E000276"),
    ("ordinary", "wzr2821", "conversation_0009", "E000047"),
]


def main() -> None:
    wanted = {(participant, conversation, event): stratum for stratum, participant, conversation, event in SELECTIONS}
    found = {}
    for line in SOURCE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            continue
        for unit in chunk["scan_units"]:
            key = (chunk["participant_id"], chunk["conversation_id"], unit["target_user_event_id"])
            if key not in wanted:
                continue
            found[key] = {
                **unit,
                "participant_id": chunk["participant_id"],
                "conversation_id": chunk["conversation_id"],
                "source_path": chunk["source_path"],
                "sampling_stratum": wanted[key],
            }
    missing = [key for _, *parts in SELECTIONS if (key := tuple(parts)) not in found]
    if missing:
        raise RuntimeError(f"Missing selected events: {missing}")
    rows = []
    for index, (_, participant, conversation, event) in enumerate(SELECTIONS, start=1):
        row = dict(found[(participant, conversation, event)])
        row["holdout_id"] = f"{HOLDOUT_PREFIX}-{index:03d}"
        rows.append(row)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candidate_path = OUT_DIR / f"locked_candidates_{len(rows)}.jsonl"
    candidate_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    review = [f"# {PROMPT_VERSION} prospective holdout — human review source", ""]
    for row in rows:
        review.extend(
            [
                f"## {row['holdout_id']} · {row['sampling_stratum']}",
                "",
                "### Prior user context",
                "",
                *(f"- `{item['event_id']}` {item['text']}" for item in row.get("prior_user_context", [])),
                "",
                "### Prior Agent context",
                "",
                *(f"- `{item['event_id']}` {item['text']}" for item in row.get("immediately_preceding_agent_context", [])),
                "",
                "### Target user event",
                "",
                row["target_user_text"],
                "",
            ]
        )
    (OUT_DIR / "human_review_source.md").write_text("\n".join(review), encoding="utf-8")
    digest = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    manifest = {
        f"created_before_{PROMPT_VERSION.replace('.', '')}_prompt": True,
        "selection_basis": "Deterministic stratified selection from previously unused raw Trace events; labels not yet created.",
        "counts": {name: sum(row["sampling_stratum"] == name for row in rows) for name in ["failure", "governance", "ordinary"]},
        "participants": len({row["participant_id"] for row in rows}),
        "sha256": digest,
    }
    (OUT_DIR / "selection_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
