"""Persistent LangGraph workflow; approval is separate from the planning agent."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .planners import agent_plan, baseline
from .simulation import cancel_driver, confirmations_complete, reserve, validate_plan


class DispatchState(TypedDict, total=False):
    world: dict
    donation: dict
    planner: str
    plan: dict
    previous_plan: dict
    status: str
    approved: bool
    cancel_driver_id: str
    event_applied: bool
    history: list[dict]
    errors: list[str]


def build_graph(checkpointer=None, model=None):
    def plan(state):
        planner = baseline if state["planner"] == "baseline" else lambda w, d: agent_plan(w, d, model=model)
        candidate = planner(state["world"], state["donation"])
        errors = validate_plan(state["world"], state["donation"], candidate) if candidate["status"] == "proposed" else []
        return {"plan": candidate, "approved": False, "errors": errors,
                "status": "invalid_plan" if errors else candidate["status"],
                "history": state.get("history", []) + [{"event": "plan", "plan": candidate}]}

    def approval(state):
        if not confirmations_complete(state["world"], state["plan"]):
            return {"status": "awaiting_confirmation", "approved": False}
        decision = interrupt({"kind": "approve_dispatch", "plan": state["plan"],
                              "message": "Approve these simulated pickup reservations?"})
        accepted = isinstance(decision, dict) and decision.get("approved") is True
        return {"approved": accepted, "status": "approved" if accepted else "rejected"}

    def schedule(state):
        # Always revalidate against current state immediately before committing.
        try:
            world = reserve(state["world"], state["donation"], state["plan"])
        except ValueError as exc:
            return {"status": "invalid_plan", "errors": [str(exc)]}
        return {"world": world, "status": "scheduled",
                "history": state.get("history", []) + [{"event": "scheduled", "approved": True}]}

    def event(state):
        return {"world": cancel_driver(state["world"], state["donation"], state["cancel_driver_id"]),
                "previous_plan": state["plan"],
                "event_applied": True, "approved": False, "status": "replanning",
                "history": state.get("history", []) + [{"event": "driver_cancelled", "driver_id": state["cancel_driver_id"]}]}

    graph = StateGraph(DispatchState)
    for name, fn in (("plan", plan), ("approval", approval), ("schedule", schedule), ("event", event)):
        graph.add_node(name, fn)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", lambda s: "approval" if s["status"] == "proposed" else END)
    graph.add_conditional_edges("approval", lambda s: "schedule" if s.get("approved") else END)
    graph.add_conditional_edges("schedule", lambda s: "event" if s["status"] == "scheduled" and s.get("cancel_driver_id") and not s.get("event_applied") else END)
    graph.add_edge("event", "plan")
    return graph.compile(checkpointer=checkpointer)
