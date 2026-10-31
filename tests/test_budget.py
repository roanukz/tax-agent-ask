"""sim.budget.BudgetedClient: what a call that fails costs, offline.

The fakes make one attempt per call; a real anthropic client over a mock
transport shows the guard counting each of the client's own retries.
"""

import anthropic
import httpx2
import pytest

from ledger.spend import cost_usd, load_prices
from sim import budget
from sim.budget import BudgetedClient, BudgetExceeded
from sim.fakes import FakeUsage, ScriptedClient

PRICES = load_prices()
HAIKU = "claude-haiku-5-5"
REQUEST = {"model": HAIKU, "max_tokens": 1024, "messages": [{"role": "user", "content": "x" * 3000}]}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def status_error(cls: type[anthropic.APIStatusError], code: int) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("fake", response=httpx2.Response(code, request=request), body=None)


def raises(error: BaseException):
    def reply(kwargs):
        raise error

    return reply


@pytest.mark.anyio
@pytest.mark.parametrize("error", [
    status_error(anthropic.RateLimitError, 429),
    status_error(anthropic.InternalServerError, 500),
    status_error(anthropic.BadRequestError, 400),
], ids=["429", "500", "400"])
async def test_a_call_the_api_answers_with_an_error_status_costs_nothing(error):
    inner = ScriptedClient([raises(error), '{"answer": "ok"}'], usage=FakeUsage(input_tokens=600, output_tokens=40))
    guard = BudgetedClient(inner, model=HAIKU, prices=PRICES, limit_usd=1.0)
    with pytest.raises(type(error)):
        await guard.messages.create(**REQUEST)
    assert guard.spent_usd == 0.0 and guard.unknown_usd == 0.0 and guard.in_flight_usd == pytest.approx(0)
    await guard.messages.create(**REQUEST)
    assert guard.spent_usd == cost_usd(HAIKU, {"input_tokens": 600, "output_tokens": 40}, PRICES), "only the billed call"


@pytest.mark.anyio
@pytest.mark.parametrize("error", [
    anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")),
    anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")),
    TimeoutError("cut off"),
], ids=["timeout", "connection", "canceled-or-other"])
async def test_a_call_whose_usage_is_unknown_still_counts_at_its_bound(error):
    guard = BudgetedClient(ScriptedClient([raises(error)]), model=HAIKU, prices=PRICES, limit_usd=1.0)
    with pytest.raises(type(error)):
        await guard.messages.create(**REQUEST)
    assert guard.spent_usd == pytest.approx(budget.call_bound_usd(REQUEST, HAIKU, PRICES), abs=1e-6)


# The anthropic client's own retries: each HTTP attempt is checked and counted.

def answered(model: str = HAIKU) -> httpx2.Response:
    return httpx2.Response(200, json={
        "id": "msg_fake", "type": "message", "role": "assistant", "model": model, "stop_reason": "end_turn",
        "stop_sequence": None, "content": [{"type": "text", "text": "ok"}],
        "usage": {"input_tokens": 600, "output_tokens": 40}})


def status(code: int, *, retry: bool = True) -> httpx2.Response:
    return httpx2.Response(code, json={"type": "error", "error": {"type": "rate_limit_error", "message": "fake"}},
                           headers={"x-should-retry": "true" if retry else "false"})


def no_answer(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ReadTimeout("fake: sent, and no answer in time", request=request)


def anthropic_over(*answers, max_retries: int = 4) -> tuple[anthropic.AsyncAnthropic, list[int]]:
    """A real anthropic client whose HTTP attempts get answers in order; also the list each attempt is logged to."""
    sent: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(len(request.content))
        answer = answers[len(sent) - 1]
        return answer(request) if callable(answer) else answer

    client = anthropic.AsyncAnthropic(api_key="placeholder-not-a-key", base_url="https://api.anthropic.com",
                                      max_retries=max_retries,
                                      http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    return client, sent


@pytest.fixture
def no_backoff(monkeypatch):
    monkeypatch.setattr(anthropic.AsyncAnthropic, "_calculate_retry_timeout", lambda self, *args, **kwargs: 0.0)


BOUND = budget.call_bound_usd(REQUEST, HAIKU, PRICES)
BILLED = cost_usd(HAIKU, {"input_tokens": 600, "output_tokens": 40}, PRICES)


@pytest.mark.anyio
async def test_an_attempt_that_got_no_answer_counts_at_its_bound_even_when_the_retry_gets_an_error_status(no_backoff):
    client, sent = anthropic_over(no_answer, status(429, retry=False))
    guard = BudgetedClient(client, model=HAIKU, prices=PRICES, limit_usd=1.0)
    with pytest.raises(anthropic.RateLimitError):
        await guard.messages.create(**REQUEST)
    assert len(sent) == 2 and guard.spent_usd == pytest.approx(BOUND, abs=1e-6), "the timed-out attempt may be billed"
    assert guard.in_flight_usd == pytest.approx(0)


@pytest.mark.anyio
async def test_a_retry_that_succeeds_adds_its_usage_to_the_attempts_before_it(no_backoff):
    client, sent = anthropic_over(status(429), no_answer, answered())
    guard = BudgetedClient(client, model=HAIKU, prices=PRICES, limit_usd=1.0)
    await guard.messages.create(**REQUEST)
    assert len(sent) == 3 and guard.calls == 1
    assert guard.known_usd == BILLED and guard.unknown_usd == pytest.approx(BOUND, abs=1e-6), \
        "the 429 costs nothing, the timeout its bound, the answer its usage"


@pytest.mark.anyio
async def test_a_retry_that_could_pass_the_limit_is_never_sent(no_backoff):
    client, sent = anthropic_over(no_answer, answered())
    guard = BudgetedClient(client, model=HAIKU, prices=PRICES, limit_usd=BOUND * 1.5)
    with pytest.raises(BudgetExceeded):
        await guard.messages.create(**REQUEST)
    assert len(sent) == 1, "the client's own retry was checked before it went out"
    assert guard.spent_usd == pytest.approx(BOUND, abs=1e-6) and guard.in_flight_usd == pytest.approx(0)
