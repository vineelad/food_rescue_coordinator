from copy import deepcopy
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from food_rescue.dashboard import decide_rescue, load_rescue, recent_rescues, start_rescue
from food_rescue.scenarios import scenarios


@pytest.fixture(autouse=True)
def local_dashboard(monkeypatch, tmp_path):
    monkeypatch.setenv("FOOD_RESCUE_DB", str(tmp_path / "dashboard.sqlite"))
    monkeypatch.setenv("LANGSMITH_TRACING", "false")


def case(name):
    return deepcopy(next(c["inputs"] for c in scenarios() if c["name"] == name))


def test_approval_is_persistent_and_duplicate_decision_is_rejected():
    rescue = start_rescue(case("split_donation"), "baseline")
    assert rescue["pending_approval"]
    assert not rescue["state"]["world"]["reservations"]
    approved = decide_rescue(rescue["thread_id"], True, rescue["checkpoint_id"])
    assert approved["state"]["status"] == "scheduled"
    assert load_rescue(rescue["thread_id"])["state"]["status"] == "scheduled"
    assert recent_rescues()[0]["thread_id"] == rescue["thread_id"]
    with pytest.raises(ValueError, match="changed"):
        decide_rescue(rescue["thread_id"], True, rescue["checkpoint_id"])


def app():
    return AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=30).run()


def plan_from_app(at, scenario="split_donation"):
    at.selectbox(key="planner_choice").select("Nearest-shelter baseline")
    at.selectbox(key="scenario_choice").select(scenario).run()
    next(button for button in at.button if button.label == "Find a rescue plan →").click().run()
    assert not at.exception
    return at.session_state.rescue


def test_app_approve_pickups_and_refresh():
    at = app()
    assert not at.exception
    rescue = plan_from_app(at)
    assert rescue["pending_approval"]
    at.button(key="approve_pickups").click().run()
    assert not at.exception
    assert at.session_state.rescue["state"]["status"] == "scheduled"
    assert not at.session_state.rescue["pending_approval"]
    assert sum(r["quantity"] for r in at.session_state.rescue["state"]["world"]["reservations"]) == 60
    at.button(key="refresh_plan").click().run()
    assert at.session_state.rescue["state"]["status"] == "scheduled"


def test_app_decline_leaves_no_reservations():
    at = app()
    plan_from_app(at)
    at.button(key="decline_plan").click().run()
    assert not at.exception
    assert at.session_state.rescue["state"]["status"] == "rejected"
    assert not at.session_state.rescue["state"]["world"]["reservations"]


def test_app_cancellation_requires_second_approval():
    at = app()
    plan_from_app(at, "driver_cancellation")
    at.button(key="approve_pickups").click().run()
    assert not at.exception
    revised = at.session_state.rescue
    assert revised["pending_approval"]
    assert revised["state"]["event_applied"]
    assert not revised["state"]["world"]["reservations"]
    at.button(key="approve_pickups").click().run()
    assert not at.exception
    assert at.session_state.rescue["state"]["status"] == "scheduled"
    assert all(r["driver_id"] != "d1" for r in at.session_state.rescue["state"]["world"]["reservations"])


def test_app_evaluation_does_not_approve_active_rescue():
    at = app()
    original = plan_from_app(at)
    at.button(key="run_eval").click().run()
    assert not at.exception
    assert len(at.session_state.eval_rows) == 8
    assert at.session_state.rescue["checkpoint_id"] == original["checkpoint_id"]
    assert at.session_state.rescue["pending_approval"]
