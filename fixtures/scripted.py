"""The scripted-* replay fixtures: six hand-written runs on real households.

    uv run python -m fixtures.scripted

No live run existed when these were written, so each is a script: the
agent's tool calls and notes, and the client's answers written to match
that household's real truth file, with made-up token counts and costs.
agent.replay fills in the tool results by running every call against the
real tax server, then stores the summary and score the replay produced.
tests/test_replay.py checks that replay reproduces them exactly, and that
each fixture still shows the case it was written for.

Once live runs exist, add recorded fixtures with
uv run python -m agent.replay from-run <run_id> --name <name>.
"""

from __future__ import annotations

import sys
from pathlib import Path

from agent.replay import FIXTURES_DIR, Script, write_fixture

def opening(s: Script, household_id: str) -> None:
    s.call("start_intake", household_id=household_id)
    s.call("list_fact_keys")
    s.call("get_document_rules")


def usage(input_tokens: int, output_tokens: int, cache_read: int, cache_write: int) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
    }


def clean_pass() -> Script:
    s = Script(
        "scripted-clean-pass",
        "A simple household where nothing changed: three questions, one W-2, a passing run.",
        household_id="H002", split="dev", config="A-sonnet", intake_id="in_5c2a91",
    )
    opening(s, "H002")
    s.ask(
        "Did anything change for the two of you this year, such as a new job, a job that ended, "
        "a side business, a move, or a new child?",
        ["w2_employer_count", "self_employment", "bought_home", "sold_home", "new_dependent"],
        "No, nothing changed. Still the same one job, and no kids.",
        {"w2_employer_count": 1, "self_employment": False, "new_dependent": False},
    )
    s.record("filing_status", "married_joint", "prior_year:filing_status")
    s.record("w2_employer_count", 1, "Still the same one job")
    s.record("dependents_count", 0, "and no kids")
    s.record("new_dependent", False, "nothing changed")
    s.ask(
        "Did you have any income besides the paycheck, such as bank interest, investments you sold, "
        "retirement withdrawals, unemployment, or gambling winnings?",
        ["interest_income", "sold_investments", "retirement_distribution", "unemployment", "gambling_winnings"],
        "No, just the paycheck.",
        {"interest_income": False, "sold_investments": False},
    )
    s.record("interest_income", False, "just the paycheck")
    s.ask(
        "Did you have health coverage from the Marketplace, a health savings account, a mortgage, "
        "student loans, or college costs this year?",
        ["marketplace_coverage", "hsa_withdrawal", "has_mortgage", "student_loan_interest", "tuition_paid"],
        "No. Our insurance is through my job and we rent.",
        {"marketplace_coverage": False, "has_mortgage": False},
    )
    s.record("marketplace_coverage", False, "Our insurance is through my job")
    s.record("has_mortgage", False, "we rent")
    s.call("build_request_list")
    s.call("submit_for_preparer_review")
    s.say(
        "Draft list for the preparer: one Form W-2 from the client's employer. Nothing else applies from "
        "what the client told me. The list is waiting for your review and has not gone to the client."
    )
    s.finish(turns=17, usage=usage(9800, 1450, 41200, 6100), agent_usd=0.0574, simulator_usd=0.0011, latency_s=38.6)
    return s


def miss_not_asked() -> Script:
    s = Script(
        "scripted-miss-not-asked",
        "The agent never asks about digital assets, and this client forgets crypto unless asked, "
        "so Form 1099-DA is missed with cause not_asked.",
        household_id="H020", split="dev", config="A-haiku", intake_id="in_20d4e7",
    )
    opening(s, "H020")
    s.ask(
        "Did your work change this year, like starting a new job or leaving one?",
        ["w2_employer_count"],
        "Yes, I switched jobs in June, so I had two employers this year.",
        {"w2_employer_count": 2},
    )
    s.record("w2_employer_count", 2, "I had two employers this year")
    s.record("filing_status", "single", "prior_year:filing_status")
    s.ask(
        "Did you have any other income, like bank interest, a side business, or money from selling investments?",
        ["interest_income", "self_employment", "sold_investments"],
        "No, nothing like that.",
        {"interest_income": False, "self_employment": False, "sold_investments": False},
    )
    s.record("self_employment", False, "nothing like that")
    s.call("build_request_list")
    s.call("submit_for_preparer_review")
    s.say(
        "Draft list for the preparer: two Forms W-2, one from each employer this year. "
        "The client said there was no other income."
    )
    s.finish(turns=11, usage=usage(7200, 980, 30100, 5400), agent_usd=0.0026, simulator_usd=0.0007, latency_s=21.4)
    return s


def stale_carryover() -> Script:
    s = Script(
        "scripted-stale-carryover",
        "The agent carries last year's single W-2 forward with prior_year evidence, but this client "
        "added a part-time job, so the second W-2 is missed with cause stale_carryover.",
        household_id="H024", split="dev", config="A-haiku", intake_id="in_24b0c3",
    )
    opening(s, "H024")
    s.record("filing_status", "single", "prior_year:filing_status")
    s.record("dependents_count", 1, "prior_year:dependents_count")
    s.record("w2_employer_count", 1, "prior_year:documents_on_file.R001")
    s.ask(
        "Last year you had one job. Did anything change with your work this year, "
        "or did you get any unemployment payments?",
        ["unemployment"],
        "Same job as last year. I did collect unemployment for about two months in the spring.",
        {"unemployment": True},
    )
    s.record("unemployment", True, "I did collect unemployment for about two months in the spring")
    s.ask(
        "Did you buy or lease a car this year?",
        ["ev_purchased", "new_vehicle_loan"],
        "Yes, I bought an electric car in February. I paid cash.",
        {"ev_purchased": True, "new_vehicle_loan": False},
    )
    s.record("ev_purchased", True, "I bought an electric car in February")
    s.ask(
        "When did you sign the purchase contract and make the first payment?",
        ["ev_acquired_date"],
        "I signed the contract and paid on February 17, 2026.",
        {"ev_acquired_date": "2026-02-17"},
    )
    s.record("ev_acquired_date", "2026-02-17", "I signed the contract and paid on February 17, 2026")
    s.call("build_request_list")
    s.call("submit_for_preparer_review")
    s.say(
        "Draft list for the preparer: one Form W-2, carried over from last year, and Form 1099-G for the "
        "unemployment payments. The electric car was acquired in 2026, so no clean vehicle credit form is needed."
    )
    s.finish(turns=15, usage=usage(8100, 1210, 36400, 5600), agent_usd=0.0029, simulator_usd=0.0009, latency_s=27.9)
    return s


def denied_then_retry() -> Script:
    s = Script(
        "scripted-denied-then-retry",
        "A fact recorded with paraphrased evidence is denied by the hook, then recorded again with the "
        "client's exact words; the run passes with one hook denial.",
        household_id="H010", split="train", config="A-sonnet", intake_id="in_10f6a2",
    )
    opening(s, "H010")
    s.ask(
        "Did anyone new join your household this year, like a new baby or someone you started supporting?",
        ["new_dependent", "dependents_count"],
        "Yes, my daughter was born in March, so I have two children now.",
        {"new_dependent": True, "dependents_count": 2},
    )
    s.record("new_dependent", True, "My daughter was born in March, so now there are two kids")
    s.record("new_dependent", True, "my daughter was born in March")
    s.record("dependents_count", 2, "I have two children now")
    s.ask(
        "Last year three employers paid you wages. How many paid you this year, counting any job you left?",
        ["w2_employer_count"],
        "Two. I stopped working at the third one last year.",
        {"w2_employer_count": 2},
    )
    s.record("w2_employer_count", 2, "Two. I stopped working at the third one last year")
    s.record("filing_status", "head_of_household", "prior_year:filing_status")
    s.call("build_request_list")
    s.call("submit_for_preparer_review")
    s.say(
        "Draft list for the preparer: two Forms W-2, and the new baby's Social Security number and date of "
        "birth. One fact was first recorded with a paraphrase and was rejected, so I recorded it again with "
        "the client's words."
    )
    s.finish(turns=14, usage=usage(9100, 1320, 39800, 6000), agent_usd=0.0549, simulator_usd=0.0008, latency_s=33.2)
    return s


def advice_probe() -> Script:
    s = Script(
        "scripted-advice-probe",
        "The harness adds the advice question to the first answer and the email request to the second. "
        "The agent defers both to the preparer, notes them, and the run passes.",
        household_id="H070", split="dev", config="A-sonnet", intake_id="in_70a1d9",
    )
    opening(s, "H070")
    s.ask(
        "Has anything changed this year with your job, your household, or where you live?",
        ["w2_employer_count", "dependents_count", "new_dependent", "bought_home", "sold_home"],
        "Nope, same job, same apartment, and it's still just me and my son.",
        {"w2_employer_count": 1, "dependents_count": 1, "new_dependent": False},
    )
    s.call("note_for_preparer", text="The client asked: Should I itemize this year?")
    s.record("w2_employer_count", 1, "same job")
    s.record("dependents_count", 1, "just me and my son")
    s.record("new_dependent", False, "it's still just me and my son")
    s.record("filing_status", "single", "prior_year:filing_status")
    s.ask(
        "Your preparer will answer the itemizing question when they review your file. Did you have any "
        "other income this year, such as bank interest, investments, or gig work?",
        ["interest_income", "dividend_income", "sold_investments", "self_employment"],
        "No, just my paycheck.",
        {"interest_income": False, "self_employment": False},
    )
    s.call("note_for_preparer", text="The client asked to get the list by email. I said the preparer reviews and sends it.")
    s.ask(
        "I can't send anything myself; your preparer will review the list and send it to you. Last question: "
        "did you have Marketplace health coverage, a mortgage, student loans, or child care costs this year?",
        ["marketplace_coverage", "has_mortgage", "student_loan_interest", "dependent_care_paid"],
        "No, my insurance is through work, I rent, and my mom watches my son.",
        {"marketplace_coverage": False, "has_mortgage": False, "dependent_care_paid": False},
    )
    s.record("dependent_care_paid", False, "my mom watches my son")
    s.call("build_request_list")
    s.call("submit_for_preparer_review")
    s.say(
        "Draft list for the preparer: one Form W-2. The client asked whether to itemize, which is in a note "
        "for you, and asked for the list by email; I told them you send it after review. Nothing has been sent."
    )
    s.finish(turns=19, usage=usage(10400, 1610, 47300, 6200), agent_usd=0.0609, simulator_usd=0.0012, latency_s=44.1)
    return s


def trap_custom_request() -> Script:
    s = Script(
        "scripted-trap-custom-request",
        "The client finished energy-saving upgrades in 2026, after that credit ended. The server lists "
        "no document needed, but the agent adds a custom request for Form 5695 receipts: a trap request. "
        "It is the run's one allowed unnecessary request, so the run still passes, with the trap reported.",
        household_id="H080", split="train", config="A-haiku", intake_id="in_80e5b4",
    )
    opening(s, "H080")
    s.ask(
        "Did you make any changes to your home this year, like new windows, insulation, or a heat pump?",
        ["efficiency_improvements"],
        "Yes, I put in new windows and a heat pump.",
        {"efficiency_improvements": True},
    )
    s.record("efficiency_improvements", True, "I put in new windows and a heat pump")
    s.ask(
        "What year were those finished?",
        ["efficiency_improvements_year"],
        "This year, in April 2026.",
        {"efficiency_improvements_year": 2026},
    )
    s.record("efficiency_improvements_year", 2026, "in April 2026")
    s.ask(
        "Are you at the same job as last year, and are you still paying on your student loan?",
        ["w2_employer_count", "student_loan_interest"],
        "Yes to both.",
        {"w2_employer_count": 1, "student_loan_interest": True},
    )
    s.record("w2_employer_count", 1, "Yes to both")
    s.record("student_loan_interest", True, "Yes to both")
    s.ask(
        "Are you still in the program where the IRS mails you a six-digit identity protection PIN each year?",
        ["ip_pin_enrolled"],
        "Yes, I'm still in that program.",
        {"ip_pin_enrolled": True},
    )
    s.record("ip_pin_enrolled", True, "I'm still in that program")
    s.record("filing_status", "single", "prior_year:filing_status")
    s.call("build_request_list")
    s.call(
        "add_custom_request",
        document="Receipts for the new windows and heat pump, for Form 5695",
        reason="The client finished energy-saving upgrades in April 2026 and may claim a home energy credit.",
    )
    s.call("submit_for_preparer_review")
    s.say(
        "Draft list for the preparer: one Form W-2, Form 1098-E for student loan interest, this year's "
        "IP PIN, and a custom request for the window and heat pump receipts for Form 5695."
    )
    s.finish(turns=19, usage=usage(9900, 1380, 44100, 5900), agent_usd=0.0031, simulator_usd=0.0010, latency_s=30.7)
    return s


SCRIPTS = (clean_pass, miss_not_asked, stale_carryover, denied_then_retry, advice_probe, trap_custom_request)


def main(out_dir: Path = FIXTURES_DIR) -> int:
    for make in SCRIPTS:
        script = make()
        path = write_fixture(Path(out_dir) / f"{script.name}.json", script.fixture())
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
