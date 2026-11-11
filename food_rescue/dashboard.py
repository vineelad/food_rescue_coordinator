"""Shared persistent operations for the local coordinator interface."""

import os
import sqlite3
from copy import deepcopy
from pathlib import Path
from threading import RLock
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .configuration import trace_metadata
from .workflow import build_graph

_LOCK = RLock()


def database_path() -> Path:
    return Path(os.getenv("FOOD_RESCUE_DB", ".runs/checkpoints.sqlite"))


def config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 80,
            "metadata": trace_metadata()}


def _snapshot(graph, thread_id: str) -> dict:
    snapshot = graph.get_state(config(thread_id))
    if not snapshot.values:
        raise ValueError("That rescue could not be found.")
    pending = any(task.interrupts for task in snapshot.tasks)
    return {"thread_id": thread_id, "state": dict(snapshot.values),
            "pending_approval": pending,
            "checkpoint_id": snapshot.config["configurable"]["checkpoint_id"]}


def start_rescue(inputs: dict, planner: str) -> dict:
    if planner not in {"agent", "baseline"}:
        raise ValueError("Unknown planner.")
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    thread_id = str(uuid4())
    with _LOCK, SqliteSaver.from_conn_string(str(path)) as saver:
        graph = build_graph(saver)
        graph.invoke(dict(deepcopy(inputs), planner=planner, history=[]), config(thread_id))
        return _snapshot(graph, thread_id)


def load_rescue(thread_id: str) -> dict:
    path = database_path()
    if not path.exists():
        raise ValueError("No saved rescues yet. Start a new rescue first.")
    with _LOCK, SqliteSaver.from_conn_string(str(path)) as saver:
        return _snapshot(build_graph(saver), thread_id)


def decide_rescue(thread_id: str, approved: bool, checkpoint_id: str) -> dict:
    """Reject stale/double submissions before resuming the approval interrupt."""
    with _LOCK, SqliteSaver.from_conn_string(str(database_path())) as saver:
        graph = build_graph(saver)
        current = _snapshot(graph, thread_id)
        if current["checkpoint_id"] != checkpoint_id:
            raise ValueError("This plan changed in another tab. Refresh it before deciding.")
        if not current["pending_approval"]:
            raise ValueError("This rescue is not waiting for approval.")
        graph.invoke(Command(resume={"approved": approved}), config(thread_id))
        return _snapshot(graph, thread_id)


def cancel_and_replan(thread_id: str, driver_id: str, checkpoint_id: str) -> dict:
    """Re-enter the saved event node for a coordinator-requested cancellation."""
    with _LOCK, SqliteSaver.from_conn_string(str(database_path())) as saver:
        graph = build_graph(saver)
        current = _snapshot(graph, thread_id)
        if current["checkpoint_id"] != checkpoint_id:
            raise ValueError("This plan changed in another tab. Refresh it before cancelling.")
        state = current["state"]
        if state["status"] != "scheduled" or current["pending_approval"]:
            raise ValueError("Approve a pickup plan before cancelling an assigned driver.")
        bookings = [r for r in state["world"]["reservations"]
                    if r["donation_id"] == state["donation"]["id"] and r["driver_id"] == driver_id]
        if not bookings:
            raise ValueError("Choose a driver assigned to this rescue.")
        if state["world"]["now"] >= min(r["pickup_at"] for r in bookings):
            raise ValueError("This demo supports cancellations before pickup only.")
        graph.update_state(config(thread_id), {"cancel_driver_id": driver_id, "event_applied": False},
                           as_node="schedule")
        graph.invoke(None, config(thread_id))
        return _snapshot(graph, thread_id)


def recent_rescues(limit: int = 15) -> list[dict]:
    path = database_path()
    if not path.exists():
        return []
    with _LOCK, sqlite3.connect(str(path)) as connection:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoints'").fetchone()
        if not exists:
            return []
        rows = connection.execute(
            "SELECT thread_id, MAX(checkpoint_id) AS latest FROM checkpoints "
            "WHERE checkpoint_ns = '' GROUP BY thread_id ORDER BY latest DESC LIMIT ?", (limit,)
        ).fetchall()
    rescues = []
    for thread_id, _ in rows:
        rescue = load_rescue(thread_id)
        if rescue["state"].get("plan"):
            rescues.append(rescue)
    return rescues
