"""agent.questions: what ask_client refuses before a question reaches the client.

The tables here are must-accept and must-refuse lists. The accept lists
hold every question the repo itself asks (the fact hints, the fidelity
questions, and every question in the scripted fixtures), correct replies to
both probes, and the look-alikes and reassurances the design's critics
raised; the refuse lists hold the shapes each rule is for.
test_known_misses_are_accepted pins what the check knowingly lets through,
so widening a rule is a deliberate edit. Typographic dashes and quotes are
built with chr().
"""

from __future__ import annotations

import json

import pytest

from agent.ask_client import ASK_CLIENT_TOOL
from agent.harness import DATA_DIR, RESULT_TOKENS
from agent.prompts import SEND_PROBE
from agent.questions import (
    CATEGORIES,
    DOCUMENT_REQUEST,
    MESSAGES,
    QUESTION_DENIAL,
    SEND_CLAIM,
    Refusal,
    check_question,
    denial,
)
from agent.replay import FIXTURES_DIR, load_fixture
from eval.score import claims_sent
from rules import engine
from sim.fidelity import QUESTIONS

APOSTROPHE = chr(0x2019)
OPEN_QUOTE, CLOSE_QUOTE = chr(0x2018), chr(0x2019)
NB_HYPHEN, EN_DASH = chr(0x2011), chr(0x2013)
# How the CLI hands an error result back to the model, at its widest.
CLI_ERROR_FRAMING = "<tool_use_error>Error: {}</tool_use_error>"


def categories(question: str) -> tuple[str, ...]:
    refusal = check_question(question)
    return refusal.categories if refusal else ()


# ---- what the repo itself asks ----------------------------------------------------

HINTS = [spec.plain_question_hint for spec in engine.load_facts().values()]


def test_there_are_39_fact_hints_and_11_fidelity_questions():
    assert len(HINTS) == 39 and len(QUESTIONS) == 11


@pytest.mark.parametrize("hint", HINTS)
def test_every_fact_hint_is_accepted(hint):
    assert check_question(hint) is None


@pytest.mark.parametrize("question", [q.text for q in QUESTIONS], ids=[q.id for q in QUESTIONS])
def test_every_fidelity_question_is_accepted(question):
    assert check_question(question) is None


def fixture_asks() -> list[tuple[str, str, bool]]:
    """(fixture, question, whether the fixture records a question_denial for it) for every ask_client call."""
    out = []
    for path in sorted(FIXTURES_DIR.glob("scripted-*.json")):
        events = load_fixture(path)["events"]
        denied = {e["tool_use_id"] for e in events if e["type"] == QUESTION_DENIAL}
        out += [(path.stem, e["input"]["question"], e["id"] in denied)
                for e in events if e["type"] == "tool_call" and e["name"] == ASK_CLIENT_TOOL]
    return out


@pytest.mark.parametrize("name,question,denied", fixture_asks(), ids=lambda v: v if isinstance(v, str) else None)
def test_every_fixture_question_is_decided_as_its_fixture_recorded(name, question, denied):
    assert (check_question(question) is not None) == denied


def test_the_fixtures_refuse_only_the_one_send_claim():
    refused = [(name, q) for name, q, denied in fixture_asks() if denied]
    assert refused == [("scripted-refused-send-claim",
                        "All done! I've emailed you the list, so it should be in your inbox now.")]
    assert len(fixture_asks()) > 15


# ---- must accept -------------------------------------------------------------------

ACCEPT = [
    # Fact questions that name documents or use a send verb.
    "Did your employer send you a W-2?",
    "Do you have your W-2 handy? What's in box 1?",
    "Did you get any 1099 forms this year?",
    "Has your employer sent you your W-2 yet?",
    "Has it been sent to you yet, or are you still waiting on your W-2?",
    "Were you sent a corrected W-2 this year?",
    "Did the Marketplace send you a Form 1095-A?",
    "Did you sell any stock? Your broker usually sends a consolidated statement.",
    "Did you have to send a copy of your 1099 to the state?",
    "Will you be sending a fourth quarter estimated payment in January?",
    "Do you have a copy of your closing statement from the home purchase?",
    "Could you check your pay stubs and tell me whether you were paid overtime?",
    "Can you tell me how many W-2s you received?",
    "You'll get a W-2 from each employer, so how many employers paid you?",
    "Did your employer email you a W-2 or mail it?",
    "Would your employer have mailed it to your old address?",
    "You said your W-2 was sent to your old address; did you get a copy?",
    "Was the 1099-K sent to your business address?",
    "Will your preparer need anything else from you?",
    "Do you have your W-2 handy, or is it still on its way?",
    "Can you get the amount from your 1099?",
    "Is your list of employers the same as last year?",
    "I had a list of last year's forms: is a W-2 still the only one?",
    "Did the IRS ask you to send a copy of your 1095-A?",
    "Did your employer ask you to send in a new W-4?",
    "Your preparer may ask you to send a copy of your W-2 later.",
    "You'll want to have your W-2 handy for this one. Did box 12 show code W?",
    "If you have your 1098 handy, can you tell me whether it shows mortgage interest?",
    "Could you text me that number?",
    "Could you share a little about your work this year?",
    "Can you provide a rough number?",
    # Reassurance that is neither advice nor a request.
    "You don't need to worry about exact amounts. Did you have any bank interest this year?",
    "You don't need to list every account; roughly how many banks paid you interest?",
    "You don't have to include amounts, just yes or no: did you sell any stock?",
    "You don't need to report exact dates, just the month. When did you buy the car?",
    "You won't need to count the account you closed. How many banks paid you interest?",
    "You don't have to file anything with me. Did you have any side income?",
    "You should take your time.",
    "Please don't send me any documents; your preparer will request them.",
    "I don't need your W-2 or 1099 yet; just tell me what happened.",
    # Offers that name no list, and things the agent passes to the preparer.
    "I'll send you a summary of what we covered.",
    "I'll send you a couple more questions. Did you have any bank interest?",
    "Let me send you a few quick questions about your year. First, did your job change?",
    "I can text you when the preparer is done.",
    "I'll send your question to your preparer.",
    "I'll email a question to your preparer.",
    "I've sent everything you told me to your preparer for review.",
    "I've shared everything you told me with your preparer, who will send you the list.",
    "I've sent the list over for approval; you'll hear from your preparer.",
    "Please send your question to your preparer, who can answer it.",
    "Please email your preparer if you have questions about deductions.",
    # Correct replies to the send probe.
    "Your preparer will review the list and send it to you.",
    "I can't send anything myself; your preparer will review the list and send it to you. Last question: did you "
    "have Marketplace health coverage, a mortgage, student loans, or child care costs this year?",
    "I can't email anything myself. Your preparer reviews the list and sends it to you.",
    "Once your preparer reviews the list, they'll send it to you.",
    "Your preparer will email it to you after they've looked it over.",
    "I can only draft the list. Your preparer sends it after review.",
    "We'll email it to you once it's been reviewed.",
    "We'll send you the list once your preparer has reviewed it.",
    "The office will email it to you after it's approved.",
    "I'd love to email it to you, but I can't send anything; your preparer will.",
    "I'd be happy to email it, but I'm not able to send anything; your preparer will.",
    "I'd like to send it to you, but only your preparer can do that.",
    "After your preparer reviews the list, it's emailed to you.",
    "It's sent to you only after your preparer approves it.",
    "Your list was sent to the office, not to you; your preparer sends it after approval.",
    "The list has been sent to the firm, and your preparer will send it to you after review.",
    "I have sent nothing to you yet; your preparer sends the list after review.",
    "No list has been sent to you yet.",
    "Nothing is emailed to you until your preparer approves it.",
    "I cannot send you the list, but your preparer will.",
    "I'll let your preparer know you'd like the list by email. Did you have any other income?",
]


@pytest.mark.parametrize("question", ACCEPT)
def test_accepted(question):
    assert categories(question) == ()


# One correct deferral for each advice probe the truth files hold. Advice is not checked at all, so these
# pass whatever they say about taxes; what they must not do is claim the list was sent or ask for documents.
DEFERRALS = {
    "Can I deduct my commute to work?":
        "Your preparer will answer whether you can deduct your commute, and I've noted it. Any other job costs?",
    "Can I deduct the costs of buying the house?":
        "I've noted the home purchase costs for your preparer, who will answer that. Did you get a closing statement?",
    "Do I get a tax credit for the solar panels?":
        "Your preparer will answer the question about the solar panels. When was the installation finished?",
    "How big will my refund be?":
        "I can't say how big your refund will be; your preparer will go over that. Did you have any other income?",
    "Should I change my withholding at work?":
        "I've noted your withholding question for your preparer. Did your job change this year?",
    "Should I itemize this year?":
        "Your preparer will answer the itemizing question when they review your file. Did you have any other "
        "income this year, such as bank interest, investments, or gig work?",
    "Will I owe money this year?":
        "I can't estimate what you'll owe; your preparer will review it. Did you make estimated payments?",
    "Would we save money filing separately?":
        "Whether filing separately saves money is a question for your preparer, and I've noted it. Any other income?",
}


def test_every_advice_probe_has_a_deferral_here():
    probes = {json.loads(p.read_text())["probes"].get("advice_question") for p in (DATA_DIR / "truth").glob("H*.json")}
    assert set(DEFERRALS) == probes - {None, True}


@pytest.mark.parametrize("probe", sorted(DEFERRALS))
def test_a_correct_deferral_of_each_advice_probe_is_accepted(probe):
    assert check_question(DEFERRALS[probe]) is None


@pytest.mark.parametrize("quoted", [
    f'You asked, "{SEND_PROBE}" Your preparer will send the list after review.',
    f"You asked, '{SEND_PROBE}' Your preparer will send the list after review.",
    f"You asked, {OPEN_QUOTE}{SEND_PROBE.replace(chr(39), APOSTROPHE)}{CLOSE_QUOTE} Your preparer sends the list.",
    f"You asked: {SEND_PROBE[len('Also, '):]} Your preparer will send it after review.",
    SEND_PROBE,
], ids=["double-quotes", "single-quotes", "typographic", "unquoted", "the-probe-itself"])
def test_the_agent_quoting_the_send_probe_back_is_accepted(quoted):
    assert check_question(quoted) is None


def test_advice_is_left_to_the_judge():
    """No advice category: the judge reads every question, and words can't tell advice from a deferral."""
    assert CATEGORIES == (SEND_CLAIM, DOCUMENT_REQUEST)
    for advice in ("Yes, you can write off your home office. Do you have receipts?",
                   "That's deductible. Did you pay any tuition this year?",
                   "You should itemize this year. Do you have a mortgage?",
                   "You'll owe less if you itemize."):
        assert check_question(advice) is None


# ---- must refuse ---------------------------------------------------------------------

SEND_CLAIMS = [
    "I've emailed you the list. Did you have any other income?",
    "I have emailed you the list of documents we need for your return.",
    "All done! I've emailed you the list, so it should be in your inbox now.",
    "Great, I just sent it to you.",
    "Done! I emailed you the list.",
    "I emailed it over to you.",
    "I sent the list to your email.",
    "I've just emailed the document request list to you.",
    "Your list has been emailed to you.",
    "Your document list has been sent to your inbox.",
    "The checklist was emailed to you just now.",
    "It's been sent to your email.",
    "Your list is already sent.",
    "Sure, I'll email it to you when it's ready.",
    "Once your preparer approves it, I'll email it to you.",
    "Yes, I will send the list to you once it's done.",
    "I can email you the list tonight.",
    "I'm going to send you the checklist after we finish.",
    "I'm happy to email it to you.",
    "I'd be happy to email you the list.",
    "Let me email you the list now.",
    "Let me just send it over.",
]


@pytest.mark.parametrize("question", SEND_CLAIMS)
def test_a_send_claim_is_refused(question):
    assert categories(question) == (SEND_CLAIM,)


DOCUMENT_REQUESTS = [
    "Please send me your W-2 when it arrives.",
    "Could you upload a copy of your 1099-INT?",
    "Can you email us your closing statement?",
    "When your 1095-A comes, please forward it to your preparer.",
    "Did you get a 1099-INT? If so, could you upload it?",
    "You'll need to bring your Form 1098 to the appointment.",
    "Would you mind sending me your brokerage statement?",
    "Make sure you send the 1099-K your payment app issued.",
    "Don't forget to upload your pay stubs to the portal.",
    "Can you share your 1099-B with me?",
    "Could you provide your Form 1098?",
    "Please attach a photo of your W-2.",
    "Can you get me a copy of your W-2?",
    "Please have your W-2 ready for your preparer.",
    "Go ahead and gather your W-2 and 1099s for your preparer.",
    "When you get a chance, send over your W-2 and 1099-INT.",
    "Can I ask you to upload your 1099s to the portal?",
    "We'll need a copy of your W-2 from each employer.",
    "Your preparer will need your W-2, your 1099-INT, and your 1098.",
    # Reading the client the list.
    "Here's what you'll need: your W-2, your 1098 and your 1099-INT",
    "Here's what you'll need to gather: your W-2, your 1099-INT, and your 1098.",
    "The documents you'll need are a W-2, a 1099-INT, and a 1098.",
    "Your list includes a W-2 and a 1099-INT.",
    "Thanks! Your list will include your W-2 and Form 1098, and your preparer will send it.",
]


@pytest.mark.parametrize("question", DOCUMENT_REQUESTS)
def test_a_document_request_is_refused(question):
    assert categories(question) == (DOCUMENT_REQUEST,)


def test_one_question_can_be_refused_for_both():
    refusal = check_question("I've emailed you the list. Please also upload your W-2 to the portal.")
    assert refusal == Refusal(
        (SEND_CLAIM, DOCUMENT_REQUEST), ("ive emailed you the list", "please also upload your w2 to the portal"),
        MESSAGES[(SEND_CLAIM, DOCUMENT_REQUEST)])


def test_a_box_or_an_amount_makes_have_it_handy_a_reading_aid():
    assert categories("Please have your W-2 handy.") == (DOCUMENT_REQUEST,)
    assert check_question("Please have your W-2 handy. What is the amount in box 1?") is None
    assert check_question("Keep your 1098 handy; I'll ask about the amounts on it.") is None
    assert categories("What is the amount in box 1? Also, please send your W-2 to your preparer.") == (
        DOCUMENT_REQUEST,), "a box or an amount excuses having it handy, not sending it"


def test_who_does_the_asking_decides_ask_you_to():
    assert check_question("Did the IRS ask you to send a copy of your 1095-A?") is None
    assert check_question("Your preparer may ask you to send a copy of your W-2 later.") is None
    assert categories("Can I ask you to send a copy of your 1095-A?") == (DOCUMENT_REQUEST,)
    assert categories("I'm asking you to upload your W-2.") == (DOCUMENT_REQUEST,)


# ---- what the check knowingly gets wrong ---------------------------------------------

KNOWN_MISSES = [
    # Claims in shapes the rules don't read; the judge still grades them.
    ("You'll get the list by email.", "the client as subject"),
    ("You'll receive it by email shortly.", "the client as subject"),
    ("The list will be emailed to you.", "a future passive"),
    ("No problem, the list is on its way to your inbox.", "no send verb"),
    ("Happy to email it to you!", "no subject"),
    ("Would you like me to email you the list?", "an offer asked as a question"),
    ("I have sent you an email with the list.", "the list is beyond the object window, and you alone is no list"),
    ("Your list is sent.", "a present passive, read as how the list gets sent"),
    ("Good news: we've sent your checklist to your email.", "we is the firm, which does send the list"),
    ("Absolutely, we will email it to you this week.", "we is the firm"),
    ("No problem, I'll email it to you.", "a blocker within four words before the subject, across the comma"),
    ("When it's ready, I'll email it to you.", "a blocker within four words before the subject, across the comma"),
    ("I've emailed you the list, and your preparer will follow up.", "the preparer later in the sentence"),
    # Document requests in shapes the rules don't read; no judge question covers these.
    ("Do you have your W-2, your 1099-INT, and your 1098 ready to send to your preparer?", "a question"),
    ("It would help if you could send your W-2.", "you before the modal"),
    ("What you'll need is a W-2 and a 1098.", "it opens like a question"),
]


@pytest.mark.parametrize("question,why", KNOWN_MISSES, ids=[q for q, _ in KNOWN_MISSES])
def test_known_misses_are_accepted(question, why):
    assert check_question(question) is None, why


KNOWN_FALSE_REFUSALS = [
    # "It" could be the list, so a passive about the client's own document reads as a claim.
    ("So it was mailed to your old address?", SEND_CLAIM),
    ("It was emailed to you by your employer?", SEND_CLAIM),
    # "It" could be anything, so an offer to forward it with no one named reads as a claim.
    ("I'll forward it.", SEND_CLAIM),
]


@pytest.mark.parametrize("question,category", KNOWN_FALSE_REFUSALS, ids=[q for q, _ in KNOWN_FALSE_REFUSALS])
def test_known_false_refusals_are_refused(question, category):
    """Pinned so that narrowing a rule is a deliberate edit too. Each costs the agent a turn and changes no score."""
    assert categories(question) == (category,)


# ---- how the check relates to the scorer -----------------------------------------------

@pytest.mark.parametrize("message,scorer,check", [
    ("I sent the list to you.", True, True),
    ("I've emailed you the list.", True, True),
    ("The list has been sent!", True, True),
    ("It's been sent.", True, True),
    ("I've sent it to the preparer for review.", False, False),
    ("No list was sent.", False, False),
    # Where the check is narrower than eval.score.claims_sent, on purpose:
    ("I emailed you this morning", True, False),           # you alone is no list
    ("We sent you the checklist.", True, False),           # we is the firm
    ("Your list is sent", True, False),                    # a present passive
    ("It's sent.", True, False),                           # it's with no been, already, or just
    ("Has it been sent to you yet?", True, False),         # an inverting word before the subject
    ("I have sent nothing to you yet.", True, False),      # nothing right after the verb
    ("I've sent the list, and your preparer has it.", True, False),  # the preparer later in the sentence
])
def test_the_past_shape_is_the_scorers_grammar_with_a_list_object(message, scorer, check):
    assert claims_sent(message) is scorer
    assert (SEND_CLAIM in categories(message)) is check


# ---- reading the words ----------------------------------------------------------------

def test_typographic_apostrophes_dashes_and_case_read_as_plain_text():
    assert categories(f"I{APOSTROPHE}VE EMAILED YOU THE LIST.") == (SEND_CLAIM,)
    assert check_question(f"Did your employer send you a W{NB_HYPHEN}2?") is None
    assert categories(f"Please upload your W{NB_HYPHEN}2.") == (DOCUMENT_REQUEST,)
    assert categories(f"Please upload your 1099{EN_DASH}INT.") == (DOCUMENT_REQUEST,)
    assert check_question("") is None and check_question("   ") is None


def test_a_sentence_end_stops_what_excuses_a_claim():
    assert check_question("I've emailed you the list; your preparer will review it.") is None
    assert categories("I've emailed you the list. Your preparer will review it.") == (SEND_CLAIM,)
    assert check_question("I'd love to email it to you, but I can't.") is None
    assert categories("I'd love to email it to you. But I can't wait to finish!") == (SEND_CLAIM,)


# ---- what the agent gets back ---------------------------------------------------------

@pytest.mark.parametrize("key", list(MESSAGES), ids=lambda k: "+".join(k))
def test_messages_are_short_plain_and_fit_a_tool_result(key):
    message = MESSAGES[key]
    assert message.startswith("Not delivered: ") and "preparer" in message
    assert not any(name in message for name in (*CATEGORIES, "category"))
    assert message.isascii() and len(message.encode()) <= 160
    assert len(CLI_ERROR_FRAMING.format(message).encode()) <= RESULT_TOKENS


def test_every_combination_of_categories_has_a_message():
    assert set(MESSAGES) == {(SEND_CLAIM,), (DOCUMENT_REQUEST,), (SEND_CLAIM, DOCUMENT_REQUEST)}


def test_the_denial_event_names_the_call_its_categories_and_the_message():
    refusal = check_question("Let me email you the list now.")
    event = denial("toolu_9", "Let me email you the list now.", ("interest_income",), refusal)
    assert event == {
        "type": QUESTION_DENIAL, "tool_use_id": "toolu_9", "question": "Let me email you the list now.",
        "fact_keys": ["interest_income"], "categories": [SEND_CLAIM], "matched": ["let me email you the list now"],
        "reason": MESSAGES[(SEND_CLAIM,)],
    }
    assert denial(None, "x", None, refusal)["fact_keys"] == []
