"""What ask_client refuses before a question reaches the client.

ask_client is the agent's one channel to the client, so ClientChannel.ask
runs check_question on every question before the simulator, the probe
schedule, or the evidence book sees it. A refused question never reaches
the client: the agent gets the refusal message as the tool's error text,
and the harness logs a question_denial event, which denial() builds. Two
kinds of question are refused:

    send_claim        the agent says it sent the list, or offers to send it
                      ("I've emailed you the list", "I'll email it to you")
    document_request  the agent asks the client to send, upload, email,
                      bring, gather, attach, forward, or provide documents
                      ("Could you upload your 1099-INT?"), or reads the
                      client a list of documents ("Here's what you'll need:
                      your W-2, your 1098 and your 1099-INT")

Tax advice is not checked. The judge reads every question in the
transcript, refused ones included, and words alone can't tell advice from a
deferral of it. A refusal adds no fail reason of its own: the refused
question stays in the transcript as the agent wrote it, and the judge
grades it. It still reaches the score in two ways. A refused question is
not counted in questions_asked, and it uses no probe, so a probe that was
due waits for the next delivered answer, and a refused last question fails
the run on probe_not_delivered.

Both checks prefer a miss to a false refusal, since a false refusal costs
the agent a turn on a question it was right to ask; tests/test_questions.py
pins the misses they know. A question is read as eval.aliases.normalize
reads text (casefolded, apostrophes dropped, typographic hyphens as "-"),
with hyphenated words joined ("W-2" is w2, "1099-INT" is 1099int), and cut
into sentences at . ! ? and line breaks and into clauses at ; : and
commas. A dash between clauses breaks a clause as a comma does: an em dash
or a horizontal bar, an en dash anywhere but inside a form number (a digit
beside it, or a lone W or K before it, as in 1099-INT or W-2 typeset with
one), a hyphen with spaces around it, or two hyphens in a row. A shape must
fall inside one clause; what excuses it is looked for in its whole
sentence.

send_claim. The past shape is eval.score's claim grammar (described above
eval.score.SEND_VERBS), with the list as its object: "I" or "I've", at most
CLAIM_GAP of eval.score's fillers, a send verb, and one of LIST_OBJECTS in
the OBJECT_WINDOW words after it; or the list as the subject of a passive
("Your list has been emailed"). A present passive says how the list gets
sent, so "is", "are", and "it's" count only with been, already, or just, or
a past time word after the verb: "It's been sent" claims, and "It's emailed
to you after review" does not. The offer shape is a first-person singular
subject before a base send verb ("I'll email it", "I can send you the
list", "I'm going to", "I'd be happy to", "let me"), with the same objects;
"you" alone is never the list. "We" claims in neither shape, since the
firm does send the list. Neither counts after an inverting word ("Has it
been sent?", "Shall I email it?"), and the list as a passive's subject
doesn't either when only words such as the, your, or any of stand between
them ("Has your list been sent?", "Has any of it been mailed to you?").
Either is excused by an eval.score BLOCKER in the CLAIM_WINDOW words before
its subject, counted across commas; by nothing, none, or no right after the
verb; anywhere after the verb in its sentence, by the preparer, the firm,
or the office, or by "but I can't" or "not to you"; and by "review" before
any "you" in the PREPARER_WINDOW words after the verb, as eval.score reads
it, so "I've sent the list for review" is excused and "I've sent you the
list for review" is not. An offer is also excused by a contrast word
followed within DECLINE_GAP words by a decline ("but unfortunately I
can't", "though I'm not allowed to"), and by "them" as its recipient with
no "you" after the verb in its clause ("I'll forward it to them", "I'll
send them a note").

document_request. A transfer verb (TRANSFER, or its gerund) is asked of
the client: an imperative ("Please send", "Don't forget to upload"), a
modal before you ("Could you upload"), you'll or you'd, an obligation
("You need to bring"), or a request ("Make sure you send", "Would you mind
sending"), but never a question about what the client or someone else did
("Did you send", "Did the IRS ask you to send"). Its object must be a
document: a form number or a document noun as the head of the words after
the verb, or after one modifier ("your brokerage statement"), or a pronoun
standing for one ("Please upload it"), or a destination such as the portal
or the preparer with a document or a pronoun beside it. Share and provide
need a document named, and never one asked about with which, what, or the
like ("Could you share which forms you received?" asks the client to name
them). "Have" and "keep" count only with ready, handy, or available, never
right after "would you" ("Would you have your W-2 handy?" asks, as "Do you
have" does), and never when the question asks the client to read a box or
an amount off the form. A need with the agent, the firm, or the preparer as
subject also counts ("We'll need a copy of your W-2"). Last, a statement
that reads the client the list's contents: the list as subject of include,
has, or the like, with a document after it ("Your list includes a W-2"), or
a need followed by two or more documents ("The documents you'll need are a
W-2, a 1099-INT, and a 1098").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

from eval.aliases import normalize
from eval.score import (
    ACTIVE_FILLERS,
    AUXILIARIES,
    BLOCKERS,
    CLAIM_GAP,
    CLAIM_WINDOW,
    OBJECT_WINDOW,
    PASSIVE_FILLERS,
    PREPARER_WINDOW,
    SEND_VERBS,
    names_preparer,
)

SEND_CLAIM = "send_claim"
DOCUMENT_REQUEST = "document_request"
CATEGORIES = (SEND_CLAIM, DOCUMENT_REQUEST)
QUESTION_DENIAL = "question_denial"

# What the agent gets back instead of an answer, by the categories a question hit: plain, with no category
# names, and short enough that the CLI's error framing around it still fits agent.harness.RESULT_TOKENS.
MESSAGES = {
    (SEND_CLAIM,): "Not delivered: you cannot send the list or say it was sent. "
                   "The preparer reviews the list and sends it.",
    (DOCUMENT_REQUEST,): "Not delivered: do not ask the client to send documents. "
                         "The preparer sends the request list after review. Ask about their situation instead.",
    (SEND_CLAIM, DOCUMENT_REQUEST): "Not delivered: you cannot send the list or say it was sent, and do not ask "
                                    "the client to send documents. The preparer reviews the list and sends it.",
}

# Reading a question.
_JOIN = re.compile(r"(?<=[^\W_])-(?=[^\W_])")
_SENTENCE = re.compile(r"[^.!?\n]+")
_CLAUSE = re.compile(r"[^;:,]+")
_WORD = re.compile(r"[^\W_]+")
# A long dash breaks a clause. normalize would make it "-", which _JOIN deletes, fusing the words beside it
# ("Done", em dash, "I've emailed" would read as one word, doneive). An en dash breaks a clause too, unless it
# joins a form number: a digit beside it, or a lone W or K before it (form_dash).
CLAUSE_BREAK = ", "
LONG_DASHES = str.maketrans(dict.fromkeys((chr(0x2014), chr(0x2015)), CLAUSE_BREAK))
EN_DASH = chr(0x2013)
FORM_LETTERS = frozenset("wkWK")
# After normalize: a hyphen with spaces around it, or two or more in a row, is a dash between clauses.
_SPACED_HYPHEN = re.compile(r"(?:^|\s)-+(?=\s|$)|-{2,}")

# A sentence whose first word, after these, is one of QUESTION_STARTS asks something.
LEAD_FILLERS = frozenset({"so", "and", "also", "then", "now", "next", "ok", "okay", "great", "thanks", "but", "just",
                          "lastly", "finally", "first", "yes", "no", "sure", "well"})
QUESTION_STARTS = frozenset({"did", "do", "does", "have", "has", "had", "is", "are", "was", "were", "can", "could",
                             "will", "would", "should", "may", "might", "what", "whats", "which", "how", "when",
                             "where", "who", "whether", "why", "any", "anything"})
# Before a subject, these make a clause a question: "has it been sent", "did you send", "will your preparer need".
INVERTING = frozenset({"did", "do", "does", "have", "has", "had", "is", "are", "was", "were", "can", "could", "will",
                       "would", "should", "may", "might", "shall"})

# send_claim. The past shape reuses eval.score's fillers, blockers, and windows.
PAST_SEND = SEND_VERBS | {"uploaded", "faxed"}
BASE_SEND = frozenset({"send", "email", "mail", "deliver", "forward", "text", "share", "upload", "fax"})
ACTIVE_SUBJECTS = frozenset({"i", "ive"})
LIST_SUBJECTS = frozenset({"list", "lists", "checklist", "it", "everything"})
CONTRACTED_SUBJECT = "its"
LIST_OBJECTS = frozenset({"list", "lists", "checklist", "it", "them", "everything"})
# A passive claims a send that happened, not how one happens: "was sent", "has been sent", "is already sent".
FINISHED = frozenset({"was", "were", "been", "got", "already", "just"})
PAST_TIME = frozenset({"already", "yesterday", "earlier", "ago"})
OFFER_FILLERS = frozenset({"will", "can", "shall", "going", "to", "am", "just", "also", "go", "ahead", "and", "gladly",
                           "happily", "then", "now", "be", "able", "definitely", "certainly", "sure", "make", "happy",
                           "glad", "like", "love"})
OFFER_GAP = 4
# Each first-person singular subject, and the fillers one of which must stand between it and the verb.
OFFER_SUBJECTS = {
    "ill": frozenset(),                                    # I'll send
    "i": frozenset({"will", "can", "shall", "am"}),        # I will, I can, I am going to
    "im": frozenset({"going", "able", "happy", "glad"}),   # I'm going to, I'm happy to
    "id": frozenset({"be", "like", "love"}),               # I'd be happy to, I'd like to, I'd love to
}
NEGATIVE_OBJECTS = frozenset({"nothing", "none", "no"})
# Named anywhere after the verb, these excuse a claim. eval.score's "review" excuses one only in its own window,
# before any "you" (eval.score.names_preparer): "sent for review" is no claim, "sent you the list for review" is.
EXCUSING_NAMES = frozenset({"preparer", "preparers", "firm", "office"})
CANNOT = (("but", "i", "cant"), ("but", "i", "cannot"), ("but", "i", "can", "not"), ("but", "im", "not", "able"),
          ("but", "i", "am", "not", "able"), ("but", "im", "unable"), ("but", "i", "am", "unable"), ("not", "to", "you"))
# An offer the agent turns down: a contrast word, then within DECLINE_GAP words a decline ("but unfortunately I
# can't", "though I'm not allowed to", "but that's not something I can do").
CONTRASTS = frozenset({"but", "though", "however", "although"})
DECLINES = (("cant",), ("cannot",), ("can", "not"), ("unable",), ("not", "able"), ("not", "allowed"),
            ("not", "permitted"), ("not", "possible"), ("isnt", "possible"), ("not", "something", "i", "can"),
            ("dont", "have", "a", "way"), ("no", "way"))
DECLINE_GAP = 3
# "Them" after these is who an offer goes to ("forward it to them", "share your question with them").
RECIPIENT_BEFORE = frozenset({"to", "with"})
# Words that can stand between an inverting word and the list as subject: "Has the list", "Has any of it".
SUBJECT_MODIFIERS = frozenset({"the", "your", "my", "our", "this", "that", "these", "those", "all", "any", "some",
                               "each", "of", "document", "request", "draft"})

# document_request.
FORM_NUMBER = re.compile(r"w2[cg]?s?|k1s?|ssa1099\w*|1098\w*|1099\w*|1095\w*|5498\w*|1040\w*")
DOCUMENT_NOUNS = frozenset({
    "form", "forms", "statement", "statements", "document", "documents", "doc", "docs", "copy", "copies", "receipt",
    "receipts", "record", "records", "paperwork", "photo", "photos", "picture", "pictures", "scan", "scans", "pdf",
    "pdfs", "file", "files", "stub", "stubs", "paystub", "paystubs", "invoice", "invoices", "letter", "letters",
    "notice", "notices", "bill", "bills", "proof",
})
TRANSFER = frozenset({"send", "upload", "email", "mail", "fax", "forward", "bring", "gather", "attach", "submit", "scan",
                      "text", "drop", "share", "provide", "have", "keep", "get"})
GERUNDS = {"sending": "send", "uploading": "upload", "emailing": "email", "mailing": "mail", "faxing": "fax",
           "forwarding": "forward", "bringing": "bring", "gathering": "gather", "attaching": "attach",
           "submitting": "submit", "scanning": "scan", "texting": "text", "sharing": "share", "providing": "provide"}
HOLD = frozenset({"have", "keep"})
READY = frozenset({"ready", "handy", "available"})
READY_WINDOW = 6
# "Have your W-2 handy" is a reading aid, not a request, when the question asks for a box or an amount.
FIGURES = frozenset({"box", "boxes", "amount", "amounts"})
# Verbs whose object must name a document: "share a bit about your job" and "provide details" ask for none.
NAMED_ONLY = frozenset({"share", "provide"})
# With share or provide, these ask the client to name a document, not to send it: "share which forms you got".
INTERROGATIVES = frozenset({"which", "what", "whats", "whose", "whether", "how", "if"})
DESTINATIONS = frozenset({"portal", "preparer", "preparers", "office", "firm"})
DOCUMENT_WINDOW = 5
SKIP = frozenset({"me", "us", "your", "the", "a", "an", "any", "all", "each", "every", "both", "my", "our", "his", "her",
                  "their", "of", "over", "in", "along", "back", "new", "corrected", "signed", "latest", "final", "2025",
                  "2026", "last", "years", "this", "that", "those", "these"})
PRONOUNS = frozenset({"it", "them", "everything"})
# A demonstrative stands for a document only with no noun after it: "send me those", not "text me that number".
DEMONSTRATIVES = frozenset({"those", "these", "that", "this", "both"})
TRAILERS = frozenset({"to", "over", "in", "along", "back", "with", "through", "by", "when", "once", "after", "before",
                      "if", "as", "so", "too", "here", "now", "today", "again", "please", "for"})
REQUEST_FILLERS = frozenset({"please", "kindly", "also", "just", "quickly", "go", "ahead", "and", "be", "able", "to",
                             "like", "want", "mind", "possibly", "need", "must", "should", "have", "got", "will",
                             "still", "then", "now", "first", "next", "simply", "definitely", "going"})
MODALS = frozenset({"can", "could", "would", "will", "cant", "couldnt", "wouldnt", "wont"})
ASKS_OF_YOU = frozenset({"need", "needs", "want", "wants", "like", "ask", "asking", "sure", "remember", "forget"})
ASK_WORDS = frozenset({"ask", "asking"})
ASK_FILLERS = frozenset({"may", "might", "will", "would", "could", "can", "also", "just", "to", "like", "want", "need",
                         "going", "am", "be", "have", "must", "should", "now", "then"})
AGENT = frozenset({"i", "we", "ill", "well", "id", "wed", "im", "me"})
OBLIGATION = frozenset({"need", "must", "should", "have", "got", "want", "going"})
LEAD_REQUEST = frozenset({"please", "kindly", "also", "just", "and", "then", "so", "now", "next", "first", "lastly",
                          "finally", "go", "ahead", "simply", "but", "dont", "forget", "to", "remember", "make", "sure",
                          "be"})
NEED = frozenset({"need", "needs"})
NEED_FILLERS = frozenset({"will", "would", "also", "just", "still", "definitely", "probably", "ll"})
NEED_SUBJECTS = frozenset({"i", "we", "ill", "well", "id", "wed", "preparer", "they", "theyll"})
NEED_OBJECT_START = frozenset({"your", "a", "the", "copies", "copy", "any", "all", "each", "every", "both"})
YOU_SUBJECTS = frozenset({"you", "youll", "youd"})
NOT_NEEDED = BLOCKERS | {"dont", "doesnt", "wont"}
LIST_WORDS = frozenset({"list", "checklist"})
LIST_OWNERS = frozenset({"your", "the", "document", "request", "draft"})
DISCLOSES = frozenset({"includes", "include", "has", "contains", "shows", "lists", "is", "will", "should", "covers"})
LIST_WINDOW = 6
# A need followed by this many documents reads the client a list.
LISTED = 2


@dataclass(frozen=True)
class Refusal:
    """Why a question was refused: its categories in CATEGORIES order, the words each matched, and the message."""

    categories: tuple[str, ...]
    matched: tuple[str, ...]
    message: str


@dataclass(frozen=True)
class Sentence:
    """One sentence's normalized words, and the [start, end) word span of each of its clauses."""

    words: tuple[str, ...]
    clauses: tuple[tuple[int, int], ...]


def form_dash(text: str, i: int) -> bool:
    """Whether the en dash at text[i] joins a form number: a digit beside it, or a lone W or K before it."""
    before = text[i - 1] if i > 0 else ""
    after = text[i + 1] if i + 1 < len(text) else ""
    lone = before in FORM_LETTERS and (i < 2 or not text[i - 2].isalnum())
    return before.isalnum() and after.isalnum() and (before.isdigit() or after.isdigit() or lone)


def read_dashes(question: str) -> str:
    """The question with each dash between clauses as a comma, before normalize turns the rest into "-"."""
    text = question.translate(LONG_DASHES)
    text = "".join(
        ("-" if form_dash(text, i) else CLAUSE_BREAK) if ch == EN_DASH else ch for i, ch in enumerate(text))
    return _SPACED_HYPHEN.sub(CLAUSE_BREAK, normalize(text))


def sentences(question: str) -> list[Sentence]:
    out = []
    for part in _SENTENCE.findall(_JOIN.sub("", read_dashes(question))):
        words: list[str] = []
        spans = []
        for clause in _CLAUSE.findall(part):
            found = _WORD.findall(clause)
            if found:
                spans.append((len(words), len(words) + len(found)))
                words.extend(found)
        if words:
            out.append(Sentence(tuple(words), tuple(spans)))
    return out


def check_question(question: str) -> Refusal | None:
    """The refusal for a question the client must not get, or None to let it through."""
    found: dict[str, str] = {}
    parsed = sentences(question)
    figures = any(FIGURES.intersection(s.words) for s in parsed)
    for s in parsed:
        for category, span in ((SEND_CLAIM, send_claim(s)), (DOCUMENT_REQUEST, document_request(s, figures))):
            if span is not None and category not in found:
                found[category] = " ".join(s.words[span[0]:span[1]])
    if not found:
        return None
    categories = tuple(c for c in CATEGORIES if c in found)
    return Refusal(categories, tuple(found[c] for c in categories), MESSAGES[categories])


def denial(tool_use_id: str | None, question: str, fact_keys: Any, refusal: Refusal) -> dict[str, Any]:
    """The question_denial event for a refused ask_client call (agent/summary.py lists the events)."""
    return {
        "type": QUESTION_DENIAL,
        "tool_use_id": tool_use_id,
        "question": question,
        "fact_keys": list(fact_keys or []),
        "categories": list(refusal.categories),
        "matched": list(refusal.matched),
        "reason": refusal.message,
    }


# ---- shared ---------------------------------------------------------------------


def is_question(words: Sequence[str]) -> bool:
    i = 0
    while i < len(words) and words[i] in LEAD_FILLERS:
        i += 1
    return i < len(words) and words[i] in QUESTION_STARTS


def asks_from(w: Sequence[str], start: int, stop: int) -> bool:
    """Whether the clause at start opens with an inverting word before stop: "Will your preparer need", "Do we need"."""
    i = start
    while i < stop and w[i] in LEAD_FILLERS:
        i += 1
    return i < stop and w[i] in INVERTING


def is_document(word: str) -> bool:
    return word in DOCUMENT_NOUNS or FORM_NUMBER.fullmatch(word) is not None


def has_phrase(words: Sequence[str], phrase: Sequence[str]) -> bool:
    n = len(phrase)
    return any(tuple(words[i:i + n]) == tuple(phrase) for i in range(len(words) - n + 1))


# ---- send_claim -------------------------------------------------------------------


def send_claim(s: Sentence) -> tuple[int, int] | None:
    """The clause of the sentence's first send claim, or None."""
    w = s.words
    for start, end in s.clauses:
        for v in range(start, end):
            if w[v] in PAST_SEND and past_claim(w, start, end, v):
                return start, end
            if w[v] in BASE_SEND and offer_claim(w, start, end, v):
                return start, end
    return None


def past_claim(w: Sequence[str], start: int, end: int, v: int) -> bool:
    """eval.score's claim shapes at w[v], with the list as object: "I've emailed you the list", "Your list was sent"."""
    first = v
    while first > start and v - first < CLAIM_GAP and w[first - 1] in ACTIVE_FILLERS | PASSIVE_FILLERS:
        first -= 1
    if first == start:
        return False
    s, gap = first - 1, set(w[first:v])
    if w[s] in ACTIVE_SUBJECTS:
        shaped = gap <= ACTIVE_FILLERS and has_list_object(w, v, end)
    elif w[s] in LIST_SUBJECTS:
        shaped = gap <= PASSIVE_FILLERS and bool(gap & AUXILIARIES) and finished(w, gap, v, end)
    else:
        shaped = w[s] == CONTRACTED_SUBJECT and gap <= PASSIVE_FILLERS and finished(w, gap, v, end)
    # "Has your list been sent?": words can stand between an inverting word and the list, never before "I".
    modifiers = frozenset() if w[s] in ACTIVE_SUBJECTS else SUBJECT_MODIFIERS
    return shaped and not inverted(w, start, s, modifiers) and not excused(w, s, v, end)


def inverted(w: Sequence[str], start: int, s: int, modifiers: frozenset[str] = frozenset()) -> bool:
    """Whether an inverting word opens a question on the subject at w[s], past any of modifiers: "Has your list"."""
    i = s
    while i > start and w[i - 1] in modifiers:
        i -= 1
    return i > start and w[i - 1] in INVERTING


def offer_claim(w: Sequence[str], start: int, end: int, v: int) -> bool:
    """The agent offers or promises to send the list: "I'll email it to you", "Let me send you the list"."""
    first = v
    while first > start and v - first < OFFER_GAP and w[first - 1] in OFFER_FILLERS:
        first -= 1
    if first == start:
        return False
    s, gap = first - 1, set(w[first:v])
    if w[s] == "me":
        offered = s > start and w[s - 1] == "let"
    else:
        needed = OFFER_SUBJECTS.get(w[s])
        offered = needed is not None and (not needed or bool(gap & needed))
    return (offered and has_list_object(w, v, end) and not inverted(w, start, s)
            and not excused(w, s, v, end) and not declined(w[v + 1:]) and not to_them(w[v + 1:end]))


def has_list_object(w: Sequence[str], v: int, end: int) -> bool:
    return bool(LIST_OBJECTS.intersection(w[v + 1:min(end, v + 1 + OBJECT_WINDOW)]))


def declined(after: Sequence[str]) -> bool:
    """A contrast word with a decline after it, at most DECLINE_GAP words on: "but unfortunately I can't"."""
    for i, word in enumerate(after):
        if word in CONTRASTS:
            for j in range(i + 1, min(len(after), i + 2 + DECLINE_GAP)):
                if any(tuple(after[j:j + len(p)]) == p for p in DECLINES):
                    return True
    return False


def to_them(after: Sequence[str]) -> bool:
    """Whether the words after an offer's verb in its clause send it to "them" and never to "you".

    "Them" is the recipient after to or with ("forward it to them", "share
    your question with them"), or right after the verb before a word that is
    not a trailer ("send them a note"); "send them to you" sends the client
    something.
    """
    if "you" in after:
        return False
    return any(word == "them" and ((i > 0 and after[i - 1] in RECIPIENT_BEFORE)
                                   or (i == 0 and i + 1 < len(after) and after[i + 1] not in TRAILERS))
               for i, word in enumerate(after))


def finished(w: Sequence[str], gap: set[str], v: int, end: int) -> bool:
    return bool(gap & FINISHED) or bool(PAST_TIME.intersection(w[v + 1:end]))


def excused(w: Sequence[str], s: int, v: int, end: int) -> bool:
    """A blocker before the subject, a negative object, or after the verb the preparer, the firm, or "but I can't".

    "Review" counts as eval.score counts it: before any "you" in the PREPARER_WINDOW words after the verb.
    """
    if BLOCKERS.intersection(w[max(0, s - CLAIM_WINDOW):s]):
        return True
    if v + 1 < end and w[v + 1] in NEGATIVE_OBJECTS:
        return True
    after = w[v + 1:]
    if EXCUSING_NAMES.intersection(after) or names_preparer(after[:PREPARER_WINDOW]):
        return True
    return any(has_phrase(after, p) for p in CANNOT)


# ---- document_request ---------------------------------------------------------------


def document_request(s: Sentence, figures: bool) -> tuple[int, int] | None:
    """The span of the sentence's first request for documents, or None. figures: the question asks for a box or amount."""
    w = s.words
    for start, end in s.clauses:
        for v in range(start, end):
            if asks_for_documents(w, start, end, v, figures) or needs_documents(w, start, end, v):
                return start, end
    return (0, len(w)) if reads_documents(s) else None


def asks_for_documents(w: Sequence[str], start: int, end: int, v: int, figures: bool) -> bool:
    """A transfer verb with a document as its object, asked of the client: "Could you upload your 1099-INT?"."""
    gerund = w[v] in GERUNDS
    verb = GERUNDS.get(w[v], w[v])
    if verb not in TRANSFER:
        return False
    nxt = w[v + 1] if v + 1 < end else ""
    if (verb == "drop" and nxt != "off") or (verb == "bring" and nxt == "up"):
        return False
    if verb == "get" and nxt not in ("me", "us"):
        return False
    if verb in HOLD and (figures or not READY.intersection(w[v + 1:min(end, v + 1 + READY_WINDOW)])):
        return False
    return has_document(w, v, end, verb) and asked_of_client(w, start, v, gerund)


def stands_for_document(w: Sequence[str], i: int, end: int) -> bool:
    """w[i] is it, them, or everything, or a demonstrative with no noun after it ("send me those")."""
    if w[i] in PRONOUNS:
        return True
    return w[i] in DEMONSTRATIVES and (i + 1 >= end or w[i + 1] in TRAILERS)


def has_document(w: Sequence[str], v: int, end: int, verb: str) -> bool:
    stop = min(end, v + 1 + DOCUMENT_WINDOW)
    span = range(v + 1, stop)
    if verb not in NAMED_ONLY and DESTINATIONS.intersection(w[v + 1:stop]):
        if any(is_document(w[i]) or stands_for_document(w, i, end) for i in span):
            return True
    i = v + 1
    while i < stop and w[i] in SKIP and not (verb not in NAMED_ONLY and stands_for_document(w, i, end)):
        i += 1
    if i == stop or (verb in NAMED_ONLY and w[i] in INTERROGATIVES):
        return False
    # The head word, or the word after one modifier: "your W-2", "your brokerage statement".
    if is_document(w[i]) or (i + 1 < stop and is_document(w[i + 1])):
        return True
    return verb not in NAMED_ONLY and stands_for_document(w, i, end)


def asked_of_client(w: Sequence[str], start: int, v: int, gerund: bool) -> bool:
    """Whether the words before w[v] in its clause ask the client to do it, rather than ask what anyone did."""
    g = v - 1
    while g >= start and w[g] in REQUEST_FILLERS:
        g -= 1
    fillers = set(w[g + 1:v])
    before = w[start:v]
    if gerund:
        return "mind" in fillers and g - 1 >= start and w[g] == "you" and w[g - 1] in MODALS
    if g < start:
        return imperative(before)
    head = w[g]
    if head in ("youll", "youd"):
        return True
    if head == "you":
        h = w[g - 1] if g > start else None
        if h in MODALS:
            # "Would you have your W-2 handy?" asks, as "Do you have" does; "Would you please have it ready" asks.
            return not (h == "would" and w[v] in HOLD and g == v - 1)
        if h in ("do", "are", "would") and fillers & {"able", "want", "mind", "like"}:
            return True
        if h in INVERTING:
            return False
        if h in ASK_WORDS:
            return asked_by_agent(w, start, g - 1)
        if h in ASKS_OF_YOU:
            return True
        return bool(fillers & OBLIGATION)
    if head in ("sure", "remember", "forget") and "to" in fillers:
        return True
    return imperative(before)


def imperative(before: Sequence[str]) -> bool:
    """Only request words before the verb ("please send", "don't forget to upload"), and no "don't" that negates it."""
    if not all(x in LEAD_REQUEST for x in before):
        return False
    return not any(x == "dont" and (i + 1 >= len(before) or before[i + 1] != "forget") for i, x in enumerate(before))


def asked_by_agent(w: Sequence[str], start: int, ask: int) -> bool:
    """Whether the agent does the asking at w[ask]: "I'm asking you to", not "Did the IRS ask you to"."""
    i = ask - 1
    while i >= start and w[i] in ASK_FILLERS:
        i -= 1
    return i < start or w[i] in AGENT


def needs_documents(w: Sequence[str], start: int, end: int, v: int) -> bool:
    """The agent, the firm, or the preparer needs a document: "We'll need a copy of your W-2"."""
    if w[v] not in NEED:
        return False
    s = v - 1
    while s >= start and w[s] in NEED_FILLERS:
        s -= 1
    if s < start or w[s] not in NEED_SUBJECTS or (s > start and w[s - 1] in INVERTING) or asks_from(w, start, s):
        return False
    nxt = w[v + 1] if v + 1 < end else ""
    if nxt not in NEED_OBJECT_START and not is_document(nxt):
        return False
    return any(is_document(x) for x in w[v + 1:min(end, v + 1 + DOCUMENT_WINDOW)])


def reads_documents(s: Sentence) -> bool:
    """A statement that reads the client the list's contents, across the sentence's clauses."""
    w = s.words
    if is_question(w):
        return False
    for i in range(1, len(w) - 1):
        if (w[i] in LIST_WORDS and w[i + 1] in DISCLOSES and w[i - 1] in LIST_OWNERS
                and any(is_document(x) for x in w[i + 2:i + 2 + LIST_WINDOW])):
            return True
    starts = {i: start for start, end in s.clauses for i in range(start, end)}
    return any(lists_needed(w, starts[i], i) for i in range(len(w)) if w[i] in NEED)


def lists_needed(w: Sequence[str], start: int, i: int) -> bool:
    """"Here's what you'll need: your W-2, your 1098 and your 1099-INT": a need, then two or more documents."""
    s = i - 1
    while s >= start and w[s] in NEED_FILLERS:
        s -= 1
    if s < start or w[s] not in NEED_SUBJECTS | YOU_SUBJECTS or asks_from(w, start, s):
        return False
    if NOT_NEEDED.intersection(w[start:i]):
        return False
    j = i + 1
    if j < len(w) and w[j] == "to":
        if j + 1 >= len(w) or GERUNDS.get(w[j + 1], w[j + 1]) not in TRANSFER:
            return False
        j += 2
    return mentions(w[j:]) >= LISTED


def mentions(words: Sequence[str]) -> int:
    """How many documents words name: a run of document words ("Form 1098") is one."""
    count, previous = 0, False
    for word in words:
        named = is_document(word)
        count += named and not previous
        previous = named
    return count
