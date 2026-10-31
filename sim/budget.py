"""Spend guard: a run's model calls can never cost more than its ledger reservation.

The ledger checks the month cap once, when a run reserves its estimate. The
estimate is generous but not a hard bound, so the guard wraps the client and
checks before every call: the run's spend so far, plus the upper bound of
every call still in flight, plus this call's upper bound, must fit in the
reservation. Otherwise it raises BudgetExceeded and the call is never sent.
With that check, a run that reserved inside the month cap can't push the
month past it.

A call's upper bound prices its prompt at one token per UTF-8 byte (a token
always covers at least one byte) plus framing overhead, at the dearer of the
input and cache write rates, and its output at max_tokens.

The guard counts each HTTP attempt, not each create call. The anthropic
client retries a call itself (max_retries), so on an anthropic client the
guard runs as its middleware too, which the client calls around every
attempt: a retry is checked against the limit and claimed like a new call
before it is sent, and raises BudgetExceeded instead when it can't fit. An
attempt the API answers with an error status (such as a 429) costs nothing,
since the API bills no tokens for it. An attempt that got no answer (it
timed out, lost its connection, or was canceled) counts at its bound, since
it may have been billed and its usage is unknown, even when a later retry
succeeds or ends in an error status. A client with no middleware (the
offline fakes) makes one attempt per call, and the same rules apply to it.
"""

from __future__ import annotations

from typing import Any, Mapping

import anthropic

from ledger.spend import cost_usd
from sim.simulator import Usage

MESSAGE_OVERHEAD_TOKENS = 16
REQUEST_OVERHEAD_TOKENS = 64


class BudgetExceeded(RuntimeError):
    """The next call could push the run past its reservation, so it was not sent."""


def _texts(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    texts = []
    for block in content or []:
        if isinstance(block, Mapping):
            texts.extend(str(block.get(name) or "") for name in ("text", "thinking"))
        else:
            texts.append(str(block))
    return texts


def prompt_tokens_bound(kwargs: Mapping[str, Any]) -> int:
    """At least as many tokens as the request's prompt can use."""
    messages = list(kwargs.get("messages") or [])
    texts = _texts(kwargs.get("system"))
    for message in messages:
        texts.extend(_texts(message.get("content")))
    framing = REQUEST_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS * (len(messages) + 1)
    return sum(len(t.encode()) for t in texts) + framing


def call_bound_usd(kwargs: Mapping[str, Any], model: str, prices: Mapping[str, Any]) -> float:
    """The most one request can cost at list price."""
    prompt = prompt_tokens_bound(kwargs)
    output = int(kwargs["max_tokens"])
    as_input = cost_usd(model, {"input_tokens": prompt, "output_tokens": output}, dict(prices), prompt_tokens=prompt)
    as_write = cost_usd(model, {"cache_write_tokens": prompt, "output_tokens": output}, dict(prices), prompt_tokens=prompt)
    return max(as_input, as_write)


class _Messages:
    def __init__(self, guard: BudgetedClient) -> None:
        self._guard = guard

    async def create(self, **kwargs: Any) -> Any:
        return await self._guard.create(kwargs)


class _Claim(anthropic.Middleware):
    """One guarded call's hold on the limit, and the anthropic client's middleware around each of its HTTP attempts."""

    def __init__(self, guard: BudgetedClient, bound: float) -> None:
        self.guard = guard
        self.bound = bound
        self.held = False
        self.attempts = 0

    def take(self) -> None:
        self.guard._admit(self.bound)
        self.held = True

    def release(self, *, billed: bool) -> None:
        """Ends the attempt that holds the claim; billed counts it at its bound, since its usage is unknown."""
        if self.held:
            self.held = False
            self.guard.in_flight_usd -= self.bound
            if billed:
                self.guard.unknown_usd += self.bound

    async def handle_async(self, request: Any, call_next: Any) -> Any:
        if self.attempts:
            self.take()  # the client is retrying: checked and claimed before the request goes out
        self.attempts += 1
        try:
            response = await call_next(request)
        except BaseException:
            self.release(billed=True)  # no answer: it may have been billed
            raise
        if not response.http_response.is_success:
            self.release(billed=False)  # an error status bills no tokens
        return response


class BudgetedClient:
    """Wraps an async Anthropic-style client and holds its spend to limit_usd.

    Calls may run concurrently: each attempt claims its bound before it is
    sent, and the check and the claim happen with no await between them.
    """

    def __init__(self, client: Any, *, model: str, prices: Mapping[str, Any], limit_usd: float) -> None:
        self._client = client
        self.model = model
        self._prices = dict(prices)
        self.limit_usd = float(limit_usd)
        self.known_usd = 0.0
        self.unknown_usd = 0.0
        self.in_flight_usd = 0.0
        self.calls = 0
        self.messages = _Messages(self)

    @property
    def spent_usd(self) -> float:
        """Known cost plus the bound of every attempt whose usage is unknown."""
        return round(self.known_usd + self.unknown_usd, 6)

    def _admit(self, bound: float) -> None:
        """Claims bound for an attempt about to be sent, or raises BudgetExceeded when it could pass the limit."""
        committed = self.known_usd + self.unknown_usd + self.in_flight_usd
        if committed + bound > self.limit_usd:
            raise BudgetExceeded(
                f"the next call could cost ${bound:.6f}, and ${committed:.6f} of the "
                f"${self.limit_usd:.6f} reservation is already spent or in flight"
            )
        self.in_flight_usd += bound

    async def create(self, kwargs: Mapping[str, Any]) -> Any:
        claim = _Claim(self, call_bound_usd(kwargs, self.model, self._prices))
        with_middleware = getattr(self._client, "with_middleware", None)
        client = with_middleware(claim) if callable(with_middleware) else self._client
        claim.take()
        try:
            response = await client.messages.create(**kwargs)
        except anthropic.APIStatusError:
            claim.release(billed=False)  # an error status bills no tokens
            raise
        except BaseException:
            claim.release(billed=True)
            raise
        claim.release(billed=False)
        self.calls += 1
        usage = Usage.from_api(getattr(response, "usage", None))
        self.known_usd += cost_usd(self.model, usage.as_dict(), self._prices, prompt_tokens=usage.prompt_tokens)
        return response
