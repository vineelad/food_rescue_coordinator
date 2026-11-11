from copy import deepcopy

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from food_rescue.evaluation import run_case, score
from food_rescue.planners import agent_plan, baseline
from food_rescue.scenarios import scenarios
from food_rescue.simulation import make_donation, make_world, reserve, validate_plan
from food_rescue.workflow import build_graph


@pytest.mark.parametrize("case", scenarios(), ids=lambda c: c["name"])
def test_baseline_scenarios(case):
    original = deepcopy(case["inputs"])
    result = run_case(case["inputs"])
    metrics = score(case["inputs"], result, case["outputs"])
    assert metrics["outcome_pass"] is case["baseline_expected_pass"]
    for key in ("committed_plan_valid", "proposed_plan_valid", "approval_respected",
                "existing_bookings_preserved", "recovery_pass"):
        assert metrics[key], (case["name"], key, result)
    assert case["inputs"] == original


def test_no_commit_before_approval_and_rejection():
    graph = build_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "reject"}}
    result = graph.invoke({"world": make_world(), "donation": make_donation(), "planner": "baseline"}, config)
    assert result["__interrupt__"]
    assert result["world"]["reservations"] == []
    result = graph.invoke(Command(resume={"approved": False}), config)
    assert result["status"] == "rejected"
    assert result["world"]["reservations"] == []


def test_sqlite_resume_after_reopening(tmp_path):
    database = str(tmp_path / "state.sqlite")
    config = {"configurable": {"thread_id": "persistent"}}
    with SqliteSaver.from_conn_string(database) as saver:
        graph = build_graph(saver)
        assert graph.invoke({"world": make_world(), "donation": make_donation(), "planner": "baseline"}, config)["__interrupt__"]
    with SqliteSaver.from_conn_string(database) as saver:
        result = build_graph(saver).invoke(Command(resume={"approved": True}), config)
        assert result["status"] == "scheduled"
        assert sum(r["quantity"] for r in result["world"]["reservations"]) == 60


@pytest.mark.parametrize("mutation", ["over_capacity", "late", "double_driver", "unknown_id", "bad_quantity", "donation_overallocation"])
def test_invalid_plans_cannot_reserve(mutation):
    world, donation = make_world(), make_donation()
    plan = baseline(world, donation)
    if mutation == "over_capacity":
        plan["legs"][0]["quantity"] = 1000
    elif mutation == "late":
        plan["legs"][0]["deliver_at"] = 2000
    elif mutation == "double_driver":
        plan["legs"][1]["driver_id"] = plan["legs"][0]["driver_id"]
    elif mutation == "unknown_id":
        plan["legs"][0]["shelter_id"] = "imaginary"
    elif mutation == "bad_quantity":
        plan["legs"][0]["quantity"] = True
    elif mutation == "donation_overallocation":
        donation["quantity"] = 1
    assert validate_plan(world, donation, plan)
    with pytest.raises(ValueError):
        reserve(world, donation, plan)
    assert not world["reservations"]


def test_reserving_twice_is_rejected():
    world, donation = make_world(), make_donation()
    plan = baseline(world, donation)
    booked = reserve(world, donation, plan)
    with pytest.raises(ValueError):
        reserve(booked, donation, plan)


def test_nebius_credentials_required(monkeypatch):
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    with pytest.raises(ValueError, match="NEBIUS_API_KEY"):
        agent_plan(make_world(), make_donation())


def test_real_langchain_tool_loop_without_network():
    """Exercise create_agent and tool schemas using scripted model responses."""
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage

    class ToolModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    plan = baseline(make_world(), make_donation())
    model = ToolModel(responses=[
        AIMessage(content="", tool_calls=[{"name": "inspect_donation", "args": {}, "id": "inspect"}]),
        AIMessage(content="", tool_calls=[{"name": "validate_candidate", "args": {"legs": plan["legs"]}, "id": "validate"}]),
        AIMessage(content="", tool_calls=[{"name": "submit_plan", "args": plan, "id": "submit"}]),
        AIMessage(content="Proposal ready for review."),
    ])
    result = agent_plan(make_world(), make_donation(), model=model)
    assert result["legs"] == plan["legs"]
    assert [t["name"] for t in result["trajectory"]] == ["inspect_donation", "validate_candidate", "submit_plan"]


def test_scarce_transport_has_a_better_feasible_solution():
    case = next(c for c in scenarios() if c["name"] == "scarce_transport")
    world, donation = case["inputs"]["world"], case["inputs"]["donation"]
    better_plan = {"legs": [
        {"driver_id": "d1", "shelter_id": "s2", "quantity": 40, "pickup_at": 900, "deliver_at": 930},
        {"driver_id": "d2", "shelter_id": "s1", "quantity": 20, "pickup_at": 900, "deliver_at": 915},
    ]}
    assert not validate_plan(world, donation, better_plan)
    assert sum(r["quantity"] for r in reserve(world, donation, better_plan)["reservations"]) == 60
    assert sum(l["quantity"] for l in baseline(world, donation)["legs"]) == 40


def test_langsmith_sdk_evaluation_offline(monkeypatch):
    """Validate evaluator normalization and experiment execution without uploading."""
    from datetime import datetime, timezone
    from uuid import uuid4
    from langsmith import Client, evaluate
    from langsmith.schemas import Example
    from food_rescue.evaluation import langsmith_evaluator

    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    case = scenarios()[0]
    example = Example(id=uuid4(), dataset_id=uuid4(), created_at=datetime.now(timezone.utc),
                      inputs=case["inputs"], outputs=case["outputs"])
    experiment = evaluate(lambda inputs: run_case(inputs), data=[example],
                          evaluators=[langsmith_evaluator], upload_results=False,
                          client=Client(auto_batch_tracing=False, tracing_sampling_rate=0))
    rows = list(experiment)
    feedback = rows[0]["evaluation_results"]["results"]
    assert next(f.score for f in feedback if f.key == "outcome_pass") == 1
