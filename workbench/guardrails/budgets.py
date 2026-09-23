class BudgetExceeded(Exception):
    def __init__(self, kind: str, limit: int) -> None:
        super().__init__(f"{kind} budget of {limit} exceeded")
        self.kind = kind
        self.limit = limit


class Budget:
    def __init__(self, max_steps: int, max_tokens: int) -> None:
        if max_steps < 1 or max_tokens < 1:
            raise ValueError("budget limits must be at least 1")
        self._max_steps = max_steps
        self._max_tokens = max_tokens
        self._steps = 0
        self._tokens = 0

    def spend_step(self) -> None:
        # a rejected spend is not counted, so the totals stay within the limits
        if self._steps + 1 > self._max_steps:
            raise BudgetExceeded("steps", self._max_steps)
        self._steps += 1

    def spend_tokens(self, tokens: int) -> None:
        if tokens < 0:
            raise ValueError("token count cannot be negative")
        if self._tokens + tokens > self._max_tokens:
            raise BudgetExceeded("tokens", self._max_tokens)
        self._tokens += tokens

    @property
    def steps(self) -> int:
        return self._steps

    @property
    def tokens(self) -> int:
        return self._tokens

    @property
    def remaining_steps(self) -> int:
        return self._max_steps - self._steps

    @property
    def remaining_tokens(self) -> int:
        return self._max_tokens - self._tokens
