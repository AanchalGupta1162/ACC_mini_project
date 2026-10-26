import pytest

from arbiter import decide, nodes_needed
from config import PODS_PER_NODE, PREWARM_HEADROOM_PODS, SCALE_DOWN_COOLDOWN_SECONDS

NOW = 1000.0
LONG_AGO = 0.0


def call(reactive=None, predictive=None, current=2, schedulable=2, cordoned=2, promoted=0,
         last_change=LONG_AGO, allow_cold=True, demand=None):
    return decide(reactive, predictive, current, schedulable, cordoned, promoted, NOW, last_change,
                  allow_cold=allow_cold, demand=demand)


@pytest.mark.parametrize("replicas, nodes", [(1, 1), (4, 1), (5, 2), (8, 2), (12, 3)])
def test_nodes_needed(replicas, nodes):
    assert nodes_needed(replicas) == nodes


def test_predictive_wins_when_it_asks_for_more():
    d = call(reactive=3, predictive=7, current=3, schedulable=3, cordoned=1)
    assert (d.target, d.scale, d.uncordon, d.cordon) == (7, True, 0, 0)


def test_reactive_wins_when_it_asks_for_more():
    assert call(reactive=8, predictive=4, current=4).target == 8


def test_an_abstaining_arm_is_ignored():
    assert call(reactive=None, predictive=5).target == 5
    assert call(reactive=5, predictive=None).target == 5


def test_both_arms_abstaining_changes_nothing():
    d = call(reactive=None, predictive=None, current=12, schedulable=2, cordoned=2)
    assert (d.target, d.scale, d.uncordon, d.cordon, d.cold_create) == (None, False, 0, 0, 0)
    assert "ALL arms" in d.reason


def test_target_is_clamped_to_bounds():
    assert call(predictive=50).target == 12
    assert call(reactive=1, current=3).target == 2


def test_holds_when_target_equals_current():
    d = call(reactive=4, predictive=4, current=4)
    assert d.scale is False
    assert "hold 4" in d.reason


def test_scale_up_is_never_delayed_by_a_recent_change():
    d = call(reactive=6, current=3, last_change=NOW - 1)
    assert (d.scale, d.target) == (True, 6)


def test_scale_down_waits_for_the_scale_down_cooldown():
    d = call(reactive=2, current=6, last_change=NOW - (SCALE_DOWN_COOLDOWN_SECONDS - 5), schedulable=2, cordoned=2)
    assert d.scale is False
    assert "cooldown" in d.reason


def test_scale_down_cooldown_expires():
    d = call(reactive=2, current=6, last_change=NOW - SCALE_DOWN_COOLDOWN_SECONDS, schedulable=2, cordoned=2)
    assert d.scale is True


def test_demand_arm_can_win_the_max():
    d = call(reactive=4, predictive=None, demand=9, current=4, schedulable=3, cordoned=1)
    assert d.target == 9


def test_demand_alone_is_enough_to_act():
    assert call(reactive=None, predictive=None, demand=5, current=2).target == 5


def test_all_three_abstaining_changes_nothing():
    d = call(reactive=None, predictive=None, demand=None, current=12)
    assert (d.target, d.scale) == (None, False)


def test_a_reserve_is_promoted_before_pods_go_pending():
    # the target alone fits on 2 nodes, but target + headroom crosses the node boundary
    target = 2 * PODS_PER_NODE - PREWARM_HEADROOM_PODS + 1
    d = call(reactive=target, current=target, schedulable=2, cordoned=2)
    assert d.uncordon == 1


def test_no_promotion_while_there_is_headroom():
    target = 2 * PODS_PER_NODE - PREWARM_HEADROOM_PODS
    d = call(reactive=target, current=target, schedulable=2, cordoned=2)
    assert d.uncordon == 0


def test_headroom_does_not_flap_a_promoted_node_back():
    # the target still needs the third node once headroom is counted, so a one-pod
    # scale-down must not hand it back
    target = 2 * PODS_PER_NODE - PREWARM_HEADROOM_PODS + 1
    d = call(reactive=target, current=target + 1, schedulable=3, cordoned=1, promoted=1,
             last_change=LONG_AGO)
    assert d.cordon == 0


def test_promotes_a_reserve_when_capacity_is_short():
    d = call(reactive=12, current=8, schedulable=2, cordoned=2)
    assert (d.scale, d.uncordon, d.cold_create) == (True, 1, 0)


def test_promotes_several_reserves_for_a_big_jump():
    assert call(predictive=12, current=2, schedulable=1, cordoned=2).uncordon == 2


def test_scale_up_and_promotion_happen_together_right_after_a_change():
    d = call(reactive=12, current=8, schedulable=2, cordoned=2, last_change=NOW - 5)
    assert (d.scale, d.uncordon) == (True, 1)


def test_headroom_never_asks_for_more_nodes_than_max_replicas_needs():
    d = call(reactive=12, current=12, schedulable=3, cordoned=1)
    assert d.uncordon == 0


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


def test_no_cordon_during_scale_down_cooldown():
    d = call(reactive=3, current=12, schedulable=3, cordoned=1, promoted=1, last_change=NOW - 5)
    assert (d.scale, d.cordon) == (False, 0)
