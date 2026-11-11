"""Offline local metrics and the same evaluators uploaded as LangSmith experiments."""

from copy import deepcopy
from time import perf_counter
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from .configuration import trace_metadata
from .simulation import validate_plan
from .workflow import build_graph


def run_case(inputs: dict, planner: str = "baseline", approve: bool = True) -> dict:
    graph = build_graph(InMemorySaver())
    config = {"configurable": {"thread_id": str(uuid4())}, "recursion_limit": 80,
              "metadata": trace_metadata()}
    state = dict(deepcopy(inputs), planner=planner, history=[])
    started = perf_counter()
    result = graph.invoke(state, config=config)
    # Explicit, test-only simulated coordinator approval. CLI never auto-approves.
    for _ in range(3):
        if not result.get("__interrupt__"):
            break
        result = graph.invoke(Command(resume={"approved": approve}), config=config)
    snapshot = graph.get_state(config)
    result = dict(snapshot.values)
    result["latency_seconds"] = round(perf_counter() - started, 4)
    result["simulation_auto_approval"] = approve
    return result


def score(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    world, donation = outputs["world"], inputs["donation"]
    reservations = [r for r in world["reservations"] if r["donation_id"] == donation["id"]]
    # Remove ONLY this donation's bookings, then independently validate committed legs.
    validation_world = deepcopy(world)
    validation_world["reservations"] = [r for r in world["reservations"] if r["donation_id"] != donation["id"]]
    errors = validate_plan(validation_world, donation, {"legs": reservations}) if reservations else []
    meals = sum(r["quantity"] for r in reservations)
    history = outputs.get("history", [])
    approved = outputs.get("approved") is True and any(h["event"] == "scheduled" and h.get("approved") for h in history)
    expected_meals = reference_outputs["min_meals"]
    outcome = outputs["status"] == reference_outputs["status"] and meals >= expected_meals
    proposed = outputs.get("plan", {}).get("status") == "proposed"
    proposal_errors = validate_plan(validation_world, donation, outputs["plan"]) if proposed else []
    preservation = all(r in world["reservations"] for r in inputs["world"]["reservations"])
    recovery = not inputs.get("cancel_driver_id") or (
        outputs.get("event_applied") is True and outputs["status"] == "scheduled"
        and not any(r["driver_id"] == inputs["cancel_driver_id"] for r in reservations)
    )
    trajectories = [t for h in history if h["event"] == "plan" for t in h["plan"].get("trajectory", [])]
    return {
        "outcome_pass": bool(outcome), "committed_plan_valid": not errors,
        "proposed_plan_valid": not proposal_errors,
        "approval_respected": not reservations or approved,
        "existing_bookings_preserved": preservation, "recovery_pass": bool(recovery),
        "scheduled_meals": meals,
        "allocation_rate": meals / donation["quantity"],
        "tool_calls": len(trajectories),
        "total_tokens": sum(h["plan"].get("total_tokens", 0) for h in history if h["event"] == "plan"),
        "submission_retries": sum(h["plan"].get("submission_retries", 0) for h in history if h["event"] == "plan"),
        "latency_seconds": outputs["latency_seconds"],
    }


def langsmith_evaluator(inputs: dict, outputs: dict, reference_outputs: dict) -> list[dict]:
    return [{"key": key, "score": float(value)} for key, value in score(inputs, outputs, reference_outputs).items()]


def run_langsmith(planner: str, split: str = "dev", repetitions: int = 1):
    import os
    from langsmith import Client
    from .scenarios import scenarios

    if not os.getenv("LANGSMITH_API_KEY"):
        raise ValueError("Set LANGSMITH_API_KEY before uploading evaluations.")
    client = Client()
    dataset_name = "sf-food-rescue-v1"
    if not client.has_dataset(dataset_name=dataset_name):
        dataset = client.create_dataset(dataset_name=dataset_name,
                                       description="Fictional SF dispatch scenarios; simulated confirmations and approvals.")
        client.create_examples(dataset_id=dataset.id, examples=[
            {"inputs": c["inputs"], "reference_outputs": c["outputs"], "metadata": {"name": c["name"]},
             "split": c["split"]} for c in scenarios()
        ])
    return client.evaluate(lambda inputs: run_case(inputs, planner),
                           data=client.list_examples(dataset_name=dataset_name, splits=[split]),
                           evaluators=[langsmith_evaluator],
                           experiment_prefix=f"sf-{planner}-{split}",
                           num_repetitions=repetitions, max_concurrency=1,
                           metadata={**trace_metadata(), "planner": planner, "split": split,
                                     "simulated_approval": True, "scenario_version": "v1"})
