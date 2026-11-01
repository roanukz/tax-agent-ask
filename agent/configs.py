"""The configurations in eval/prereg.yaml: four agent setups and five baselines.

reserve_usd is what a run reserves in the ledger before its first call:
about twice the PRD section 7 per-run estimate, since conversations can run
long, and never more than RUN_CAP_USD.

max_output_tokens is an agent config's CLAUDE_CODE_MAX_OUTPUT_TOKENS: the CLI
sends it as max_tokens on every agent call, so it bounds each call's output,
and the harness holds that much output for the agent's next call before it
is sent. It must leave room for adaptive thinking, which counts toward it.
Every agent config uses 8192, so every model gets the same room per call.

The hold is a strict bound, the next prompt at the cache write rate plus the
whole output cap, and it is far above what a cached call costs. With a
20,000 to 40,000 token prompt it is about $0.26 to $0.36 on Opus, $0.13 to
$0.18 on Sonnet, and under a cent on Haiku. A run stops with run_cap once
its spend plus that hold would pass RUN_CAP_USD, so at the default $5.00 an
A-opus run can spend about $4.64 to $4.74, A-sonnet about $4.82 to $4.87,
and A-haiku nearly all of it. That is about nineteen times the PRD's doubled
Opus estimate ($0.24), so run_cap marks a runaway rather than a long
conversation; at $0.50 an A-opus run kept only $0.14 to $0.24, and at $2.00
about $1.64 to $1.74. The effective run cap still differs by model. 16384
would add another $0.16 to every Opus hold.
"""

from __future__ import annotations

from dataclasses import dataclass

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"

SIMULATOR_MODEL = HAIKU
JUDGE_MODEL = HAIKU
MAX_TURNS = 40
DEFAULT_MAX_OUTPUT_TOKENS = 8192


@dataclass(frozen=True)
class Config:
    name: str
    kind: str  # "agent", "full_organizer", "prior_year", or "model_list"
    model: str | None = None
    planner_model: str | None = None
    reserve_usd: float = 0.0
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS

    @property
    def uses_simulator(self) -> bool:
        return self.kind in ("agent", "full_organizer")


CONFIGS: dict[str, Config] = {
    c.name: c
    for c in (
        Config("A-haiku", "agent", model=HAIKU, reserve_usd=0.03),
        Config("A-sonnet", "agent", model=SONNET, reserve_usd=0.15),
        Config("A-opus", "agent", model=OPUS, reserve_usd=0.30),
        Config("A-split", "agent", model=HAIKU, planner_model=SONNET, reserve_usd=0.08),
        Config("B1", "full_organizer", reserve_usd=0.05),
        Config("B2", "prior_year", reserve_usd=0.0),
        Config("B3-haiku", "model_list", model=HAIKU, reserve_usd=0.01),
        Config("B3-sonnet", "model_list", model=SONNET, reserve_usd=0.03),
        Config("B3-opus", "model_list", model=OPUS, reserve_usd=0.06),
    )
}

JUDGE_RESERVE_USD = 0.01


def get_config(name: str) -> Config:
    if name not in CONFIGS:
        raise KeyError(f"unknown config {name!r}; known: {', '.join(CONFIGS)}")
    return CONFIGS[name]
