import pytest

from arbiter import decide, nodes_needed

NOW = 1000.0
LONG_AGO = 0.0


def call(reactive=None, predictive=None, current=2, schedulable=2, cordoned=2, promoted=0,
         last_change=LONG_AGO, allow_cold=True):
    return decide(reactive, predictive, current, schedulable, cordoned, promoted, NOW, last_change,
                  allow_cold=allow_cold)


@pytest.mark.parametrize("replicas, nodes", [(1, 1), (4, 1), (5, 2), (8, 2), (12, 3)])
def test_nodes_needed(replicas, nodes):
    assert nodes_needed(replicas) == nodes


def test_predictive_wins_when_it_asks_for_more():
    d = call(reactive=3, predictive=7, current=3)
    assert (d.target, d.scale, d.uncordon, d.cordon) == (7, True, 0, 0)


def test_reactive_wins_when_it_asks_for_more():
    assert call(reactive=8, predictive=4, current=4).target == 8


def test_an_abstaining_arm_is_ignored():
    assert call(reactive=None, predictive=5).target == 5
    assert call(reactive=5, predictive=None).target == 5


def test_both_arms_abstaining_changes_nothing():
    d = call(reactive=None, predictive=None, current=12, schedulable=2, cordoned=2)
    assert (d.target, d.scale, d.uncordon, d.cordon, d.cold_create) == (None, False, 0, 0, 0)
    assert "BOTH arms" in d.reason


def test_target_is_clamped_to_bounds():
    assert call(predictive=50).target == 12
    assert call(reactive=1, current=3).target == 2


def test_holds_when_target_equals_current():
    d = call(reactive=4, predictive=4, current=4)
    assert d.scale is False
    assert "hold 4" in d.reason


def test_cooldown_blocks_replica_changes():
    d = call(reactive=6, current=3, last_change=NOW - 5)
    assert d.scale is False
    assert "cooldown" in d.reason


def test_cooldown_expires():
    assert call(reactive=6, current=3, last_change=NOW - 20).scale is True


def test_promotes_a_reserve_when_capacity_is_short():
    d = call(reactive=12, current=8, schedulable=2, cordoned=2)
    assert (d.scale, d.uncordon, d.cold_create) == (True, 1, 0)


def test_promotes_several_reserves_for_a_big_jump():
    assert call(predictive=12, current=2, schedulable=1, cordoned=2).uncordon == 2


def test_promotes_capacity_even_during_cooldown():
    d = call(reactive=12, current=8, schedulable=2, cordoned=2, last_change=NOW - 5)
    assert (d.scale, d.uncordon) == (False, 1)


def test_falls_back_to_the_cold_path_when_the_reserve_pool_is_empty():
    d = call(reactive=12, current=12, schedulable=2, cordoned=0)
    assert (d.uncordon, d.cold_create) == (0, 1)
    assert "reserve pool EMPTY" in d.reason


def test_without_the_cold_path_an_empty_pool_only_warns():
    d = call(reactive=12, current=12, schedulable=2, cordoned=0, allow_cold=False)
    assert (d.uncordon, d.cold_create) == (0, 0)
    assert "reserve pool EMPTY" in d.reason


def test_returns_a_reserve_on_scale_down():
    d = call(reactive=3, predictive=4, current=12, schedulable=3, cordoned=1, promoted=1)
    assert (d.target, d.scale, d.cordon) == (4, True, 1)


def test_never_returns_a_reserve_when_none_is_promoted():
    assert call(reactive=3, current=8, schedulable=2, cordoned=2, promoted=0).cordon == 0


def test_no_cordon_during_cooldown():
    d = call(reactive=3, current=12, schedulable=3, cordoned=1, promoted=1, last_change=NOW - 5)
    assert (d.scale, d.cordon) == (False, 0)
