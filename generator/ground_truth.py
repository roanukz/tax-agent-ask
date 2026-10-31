"""Ground truth: the engine's answer for every household's true facts.

    uv run python -m generator.ground_truth                rebuilds data/truth/ground_truth.json only
    uv run python -m generator.ground_truth --data <dir>   reads <dir>/truth/, writes there

For each household it stores the required documents and triggered traps under
every non-excluded rule ("verified" and "to_verify"), the statuses train and
dev scoring use. Test scoring drops a household whose answer depends on a rule
that is not verified, excluded rules included (PRD 6.6), so unverified_rule_ids
lists every rule that applies to the true facts under any status and is not
verified, as eval.score.unverified_dependencies does.

rules_fingerprint is a sha256 over the parsed rules with the source fields
removed. Filling in or fixing a source leaves it unchanged, while any other
edit (a condition, a count, a trap flag, a status) changes it, which marks this
file as stale until it is rebuilt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from generator.files import DATA_DIR, GROUND_TRUTH_FILE, TRUTH, load_truths, write_json
from rules.engine import (
    DOCUMENT_RULES_PATH,
    STATUSES,
    FactSpec,
    Rule,
    load_facts,
    load_rules,
    required_documents,
    triggered_traps,
)

SOURCE_FIELDS = ("source_url", "source_title", "source_version")
GROUND_TRUTH_STATUSES = ("verified", "to_verify")


def rules_fingerprint(path: Path = DOCUMENT_RULES_PATH) -> str:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    stripped = [{k: v for k, v in rule.items() if k not in SOURCE_FIELDS} for rule in raw]
    canonical = json.dumps(stripped, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def unverified_rule_ids(facts: Mapping[str, Any], rules: list[Rule], specs: Mapping[str, FactSpec]) -> list[str]:
    """Rules that apply to these facts under any status, documents and traps alike, and are not verified."""
    applied = [d["rule_id"] for d in required_documents(facts, rules, specs, statuses=STATUSES)]
    applied += triggered_traps(facts, rules, specs, statuses=STATUSES)
    status = {r.id: r.status for r in rules}
    return sorted(rid for rid in applied if status[rid] != "verified")


def household_entry(truth: Mapping[str, Any], rules: list[Rule], specs: Mapping[str, FactSpec]) -> dict[str, Any]:
    facts = truth["true_facts"]
    return {
        "split": truth["split"],
        "complexity": truth["complexity"],
        "required": required_documents(facts, rules, specs, statuses=GROUND_TRUTH_STATUSES),
        "traps": triggered_traps(facts, rules, specs, statuses=GROUND_TRUTH_STATUSES),
        "unverified_rule_ids": unverified_rule_ids(facts, rules, specs),
    }


def build_ground_truth(
    truths: Iterable[Mapping[str, Any]],
    rules: list[Rule] | None = None,
    specs: Mapping[str, FactSpec] | None = None,
    rules_path: Path = DOCUMENT_RULES_PATH,
) -> dict[str, Any]:
    rules = rules if rules is not None else load_rules(rules_path)
    specs = specs if specs is not None else load_facts()
    ordered = sorted(truths, key=lambda t: t["household_id"])
    return {
        "rules_fingerprint": rules_fingerprint(rules_path),
        "statuses": list(GROUND_TRUTH_STATUSES),
        "households": {t["household_id"]: household_entry(t, rules, specs) for t in ordered},
    }


def write_ground_truth(data_dir: Path = DATA_DIR) -> Path:
    truths = load_truths(data_dir)
    if not truths:
        raise FileNotFoundError(f"no household truth files in {data_dir / TRUTH}")
    path = data_dir / TRUTH / GROUND_TRUTH_FILE
    write_json(path, build_ground_truth(truths.values()))
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    args = parser.parse_args(argv)
    path = write_ground_truth(args.data)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
