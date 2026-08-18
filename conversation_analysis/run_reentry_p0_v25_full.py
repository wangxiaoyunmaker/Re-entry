"""Run the frozen v2.5 P0 high-recall scan over the complete user-event corpus.

This runner deliberately keeps the frozen prompt components unchanged. It
reuses the validated P0 wrapper for XML isolation, JSON-schema validation,
compatibility fallbacks, checkpointed chunk execution, and candidate merging.
The output directory is independent from all development and holdout runs.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path

import reentry_p0_discovery as p0
import reentry_p0_optimization_v4 as opt


ROOT = p0.ROOT
RUN_DIR = ROOT / "outputs/reentry_p0_recall_20260818_v25_full"
RESULTS_PATH = RUN_DIR / "p0_v25_full_scan_results.jsonl"
CANDIDATE_PATH = RUN_DIR / "p0_v25_candidates.jsonl"
P1_INPUTS_PATH = RUN_DIR / "p0_v25_candidates_for_p1.jsonl"
REPORT_PATH = RUN_DIR / "p0_v25_full_scan_report.md"
MANIFEST_PATH = RUN_DIR / "p0_v25_run_manifest.json"

V21_SYSTEM = ROOT / "prompts/reentry_p0/v2_1_candidate/system.txt"
V23_DEVELOPER = ROOT / "prompts/reentry_p0/v2_3_candidate/developer_gate_v23.xml"
V21_FEWSHOT = ROOT / "prompts/reentry_p0/v2_1_candidate/few_shot_gate_v21.xml"
V25_ADDENDUM = ROOT / "prompts/reentry_p0/v2_5_candidate/developer_gate_v25_addendum.xml"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def install_frozen_prompt() -> None:
    """Point the existing validated wrapper at the frozen v2.5 components."""
    p0.SYSTEM_PATH = V21_SYSTEM
    p0.DEVELOPER_PATH = V23_DEVELOPER
    p0.FEWSHOT_PATH = V21_FEWSHOT
    p0.PROMPT_VERSION = "p0-v2.5-frozen"

    def render(_few_shot_path: Path = V21_FEWSHOT) -> tuple[str, bool]:
        base = V23_DEVELOPER.read_text(encoding="utf-8")
        few_shot = V21_FEWSHOT.read_text(encoding="utf-8")
        addendum = V25_ADDENDUM.read_text(encoding="utf-8")
        developer = base.replace("{{FEW_SHOT_BANK}}", few_shot) + "\n" + addendum
        return developer, True

    p0.render_developer_prompt = render
    original_parse_verdict_payload = opt.parse_verdict_payload

    def parse_verdict_payload_robust(content: str, chunk: dict) -> dict:
        """Accept a top-level list of per-target verdicts from a gateway."""
        try:
            return original_parse_verdict_payload(content, chunk)
        except ValueError as exc:
            if "root must be an object" not in str(exc):
                raise
            stripped = content.strip()
            if stripped.startswith("```"):
                first_newline = stripped.find("\n")
                if first_newline >= 0:
                    stripped = stripped[first_newline + 1 :]
                if stripped.endswith("```"):
                    stripped = stripped[:-3].strip()
            value = json.loads(stripped)
            if isinstance(value, list) and all(isinstance(item, dict) for item in value):
                return {"verdicts": value}
            raise

    opt.parse_verdict_payload = parse_verdict_payload_robust
    original_normalize_verdict = opt.normalize_singleton_verdict_output

    def normalize_verdict_robust(result: dict, chunk: dict) -> dict:
        expected_ids = [unit["target_user_event_id"] for unit in chunk.get("scan_units") or []]
        if len(expected_ids) == 1 and set(result) == {expected_ids[0]}:
            value = result[expected_ids[0]]
            if isinstance(value, str) and value in {"RETAIN_STRONG", "RETAIN_POSSIBLE", "DO_NOT_RETAIN"}:
                decision = value
                return {
                    "conversation_id": chunk["conversation_id"],
                    "chunk_id": chunk["chunk_id"],
                    "verdicts": [{
                        "target_event_id": expected_ids[0],
                        "decision": decision,
                        "signal_types": [] if decision == "DO_NOT_RETAIN" else ["OTHER"],
                        "rationale": "Normalized a singleton decision returned without the P0 wrapper object.",
                    }],
                }
        return original_normalize_verdict(result, chunk)

    opt.normalize_singleton_verdict_output = normalize_verdict_robust
    opt.CONFIGS["V25_FULL_SCAN"] = {
        "input_style": "timeline",
        "user_depth": 1,
        "agent_depth": 2,
        "user_clip": 800,
        "agent_clip": 1200,
        "chunk_size": 4,
        "system": V21_SYSTEM,
        "developer": V23_DEVELOPER,
        "developer_append": V25_ADDENDUM,
        "fewshot": V21_FEWSHOT,
        "explicit_gates": False,
        "thinking_disabled": True,
    }


def install_output_paths() -> None:
    p0.OUT_DIR = RUN_DIR
    p0.FULL_RESULTS_PATH = RESULTS_PATH
    p0.CANDIDATE_INDEX_PATH = CANDIDATE_PATH
    p0.P1_INPUTS_PATH = P1_INPUTS_PATH
    p0.REPORT_PATH = REPORT_PATH


def write_manifest(provider: str, env_path: Path) -> None:
    source = p0.SCAN_CHUNKS_PATH
    manifest = {
        "run_id": "p0-v2.5-frozen-full-20260818",
        "status": "RUNNING_OR_COMPLETE",
        "prompt_frozen": True,
        "provider": provider,
        "env_path": str(env_path),
        "input_chunks": str(source),
        "input_chunk_count": len(p0.read_jsonl(source)),
        "prompt_components": {
            "system": str(V21_SYSTEM),
            "system_sha256": sha256(V21_SYSTEM),
            "developer_base": str(V23_DEVELOPER),
            "developer_base_sha256": sha256(V23_DEVELOPER),
            "few_shot": str(V21_FEWSHOT),
            "few_shot_sha256": sha256(V21_FEWSHOT),
            "v25_addendum": str(V25_ADDENDUM),
            "v25_addendum_sha256": sha256(V25_ADDENDUM),
        },
        "outputs": {
            "results": str(RESULTS_PATH),
            "candidates": str(CANDIDATE_PATH),
            "p1_inputs": str(P1_INPUTS_PATH),
            "report": str(REPORT_PATH),
        },
        "scope": "All normalized USER events in the complete 41-participant corpus; P0 candidate retrieval only.",
        "downstream_boundary": "P0 retention is not a final Re-entry label; candidates require P1 Occasion and direct-delegation sufficiency review.",
    }
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_parallel(env_path: Path, provider: str, workers: int, limit: int | None) -> None:
    """Run resumably with bounded concurrency; only the main thread writes JSONL."""
    chunks = p0.read_jsonl(p0.SCAN_CHUNKS_PATH)
    if limit is not None:
        chunks = chunks[:limit]
    existing = {
        row["chunk_id"]: row
        for row in p0.read_jsonl(RESULTS_PATH)
        if row.get("chunk_id") and "error" not in row
    } if RESULTS_PATH.exists() else {}
    pending = [chunk for chunk in chunks if chunk["chunk_id"] not in existing]

    def execute(chunk: dict) -> tuple[str, dict]:
        try:
            # Reuse the validated optimization wrapper's singleton-verdict
            # normalization. Some gateways ignore the multi-verdict schema on
            # one-target chunks; this is a wrapper compatibility issue, not a
            # change to the frozen v2.5 prompt.
            result, meta = opt.call_verdict_api(chunk, "V25_FULL_SCAN", env_path, provider)
            meta["prompt_version"] = "p0-v2.5-frozen"
            output = {
                "chunk_id": chunk["chunk_id"],
                "source_path": chunk["source_path"],
                "participant_id": chunk["participant_id"],
                "conversation_id": chunk["conversation_id"],
                "result": result,
                "meta": meta,
                "requires_human_adjudication": True,
            }
        except Exception as exc:
            output = {
                "chunk_id": chunk["chunk_id"],
                "source_path": chunk["source_path"],
                "participant_id": chunk["participant_id"],
                "conversation_id": chunk["conversation_id"],
                "error": f"{type(exc).__name__}: {exc}",
                "requires_human_adjudication": True,
            }
        return chunk["chunk_id"], output

    print(f"Resuming {len(existing)} completed chunks; pending={len(pending)}; workers={workers}")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(execute, chunk) for chunk in pending]
        for index, future in enumerate(as_completed(futures), start=1):
            chunk_id, output = future.result()
            existing[chunk_id] = output
            p0.write_jsonl(RESULTS_PATH, [existing[key] for key in sorted(existing)])
            retained = sum(
                item.get("decision") != "DO_NOT_RETAIN"
                for item in output.get("result", {}).get("verdicts", [])
            )
            print(f"[{index}/{len(pending)}] {chunk_id}: {retained} candidates")

    if len(existing) != len(chunks):
        raise RuntimeError(f"Incomplete scan: {len(existing)}/{len(chunks)} chunks")
    p0.build_candidates(RESULTS_PATH)
    p0.write_status_report()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--provider", choices=["photomind", "deepseek"], default="photomind")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    if not p0.SCAN_CHUNKS_PATH.exists():
        raise RuntimeError(f"Missing prepared corpus chunks: {p0.SCAN_CHUNKS_PATH}")
    install_frozen_prompt()
    install_output_paths()
    write_manifest(args.provider, args.env)
    if args.workers < 1:
        raise ValueError("--workers must be >= 1")
    run_parallel(args.env, args.provider, args.workers, args.limit)
    p0.materialize_p1(args.limit)
    p0.write_status_report()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["status"] = "COMPLETE"
    manifest["result_sha256"] = sha256(RESULTS_PATH)
    manifest["candidate_sha256"] = sha256(CANDIDATE_PATH)
    manifest["p1_input_sha256"] = sha256(P1_INPUTS_PATH)
    manifest["candidate_count"] = len(p0.read_jsonl(CANDIDATE_PATH))
    manifest["result_chunk_count"] = len(p0.read_jsonl(RESULTS_PATH))
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
