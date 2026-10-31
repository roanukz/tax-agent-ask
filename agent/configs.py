"""The configurations in eval/prereg.yaml: four agent setups and five baselines.

reserve_usd is what a run reserves in the ledger before its first call:
about twice the PRD section 7 per-run estimate, since conversations can run
long, and never more than RUN_CAP_USD.

max_output_tokens is an agent config's CLAUDE_CODE_MAX_OUTPUT_TOKENS: the CLI
sends it as max_tokens on every agent call, so it bounds each call's output,
and the harness holds that much output for the agent's next call before it
is sent. It must leave room for adaptive thinking, which counts toward it.
Every agent config uses 8192, so no model gets more room than another: at
Opus prices the hold for one call is then about $0.16 of output plus its
prompt, which leaves a typical A-opus run room under a RUN_CAP_USD of
$0.50, where 16384 would stop it after a few calls.
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
