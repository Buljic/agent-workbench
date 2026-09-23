import pytest

from workbench.guardrails.budgets import Budget, BudgetExceeded


def test_three_steps_pass_fourth_raises():
    budget = Budget(3, 100)
    for _ in range(3):
        budget.spend_step()

    with pytest.raises(BudgetExceeded) as exc:
        budget.spend_step()

    assert exc.value.kind == "steps"
    assert exc.value.limit == 3
    assert budget.steps == 3


def test_token_total_over_limit_raises():
    budget = Budget(3, 100)
    budget.spend_tokens(60)

    with pytest.raises(BudgetExceeded) as exc:
        budget.spend_tokens(41)

    assert exc.value.kind == "tokens"
    assert exc.value.limit == 100
    assert budget.tokens == 60


def test_negative_tokens_rejected():
    with pytest.raises(ValueError):
        Budget(3, 100).spend_tokens(-1)


@pytest.mark.parametrize("max_steps,max_tokens", [(0, 100), (3, 0), (-1, 100), (3, -5)])
def test_limits_below_one_rejected(max_steps, max_tokens):
    with pytest.raises(ValueError):
        Budget(max_steps, max_tokens)


def test_remaining_reflects_spending():
    budget = Budget(3, 100)
    budget.spend_step()
    budget.spend_tokens(25)

    assert budget.remaining_steps == 2
    assert budget.remaining_tokens == 75
    assert budget.steps == 1
    assert budget.tokens == 25
