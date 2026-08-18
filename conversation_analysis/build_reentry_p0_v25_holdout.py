"""Build the prospective v2.5 P0 holdout selected before v2.5 prompt authoring."""

from pathlib import Path

import build_reentry_p0_v24_holdout as builder


builder.OUT_DIR = builder.ROOT / "outputs/reentry_p0_recall_20260817/p0_v25_current_theory_holdout"
builder.HOLDOUT_PREFIX = "V25H"
builder.PROMPT_VERSION = "v2.5"
builder.SELECTIONS = [
    ("failure", "fyjjz666", "conversation_0001", "E000178"),
    ("failure", "Sheila0125", "conversation_0004", "E000019"),
    ("failure", "wyuk777", "conversation_0034", "E000118"),
    ("failure", "18250459910", "conversation_0029", "E000042"),
    ("failure", "13627629387", "conversation_0010", "E000071"),
    ("failure", "wzr2821", "conversation_0001", "E000582"),
    ("governance", "srxh1683128236", "conversation_0003", "E000030"),
    ("governance", "15077877013", "conversation_0045", "E000708"),
    ("governance", "13162828717", "conversation_0004", "E000172"),
    ("governance", "CorneliaStreet233", "conversation_0010", "E000014"),
    ("governance", "zyf2492313716", "conversation_0003", "E000221"),
    ("governance", "_微信待确认_Cyber-Agent", "conversation_0001", "E000094"),
    ("ordinary", "wwen_713", "conversation_0003", "E000255"),
    ("ordinary", "_微信待确认_Lumno_志愿者", "conversation_0238", "E000001"),
    ("ordinary", "wyuk777", "conversation_0035", "E000121"),
    ("ordinary", "yaoshi1019", "conversation_0041", "E000009"),
    ("ordinary", "Pineraindew", "conversation_0001", "E000307"),
    ("ordinary", "Mina99mi", "conversation_0010", "E000004"),
    ("ordinary", "wzr2821", "conversation_0009", "E000047"),
    ("ordinary", "Hercules-Z", "conversation_0001", "E000164"),
]


if __name__ == "__main__":
    builder.main()
