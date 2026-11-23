"""D17 thresholds and the machine-actor ceiling (issue 22), pure."""

from __future__ import annotations

import pytest

from app.services.ledger import MACHINE_ACTORS, ModelCeilingError, _check_ceiling  # pyright: ignore[reportPrivateUsage]
from app.services.writeback import RuleSuggestionPolicy


def test_thresholds_parse_from_cases_correctors_days() -> None:
    assert RuleSuggestionPolicy.from_spec("1/1/30") == RuleSuggestionPolicy(1, 1, 30)
    assert RuleSuggestionPolicy.from_spec("3/2/30") == RuleSuggestionPolicy(3, 2, 30)


@pytest.mark.parametrize("spec", ["0/1/30", "1/1", "a/b/c", "1/1/0"])
def test_bad_thresholds_are_refused(spec: str) -> None:
    with pytest.raises(ValueError):
        RuleSuggestionPolicy.from_spec(spec)


@pytest.mark.parametrize("actor", sorted(MACHINE_ACTORS))
def test_a_machine_actor_stays_under_the_ceiling_until_a_person_approves(actor: str) -> None:
    with pytest.raises(ModelCeilingError, match=f"a {actor} actor"):
        _check_ceiling({"type": actor, "id": "x"}, 20, None)
    _check_ceiling({"type": actor, "id": "x"}, 10, None)
    _check_ceiling({"type": actor, "id": "x"}, 20, {"approver_type": "user", "user_id": "u"})
