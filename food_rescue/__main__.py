"""Run with python -m food_rescue. All scheduling is simulated."""

import argparse
import json
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .configuration import trace_metadata
from .evaluation import run_case, run_langsmith, score
from .scenarios import scenarios
from .workflow import build_graph


def display(result):
    def clock(value):
        return f"{value // 60:02d}:{value % 60:02d}"

    plan = result.get("plan", {})
    donation = result["donation"]
    print(f"\nSF FOOD RESCUE | FICTIONAL SIMULATION | {result['planner']}")
    print(f"Status: {result['status']}")
    print(f"Donation: {donation['quantity']} {donation['food_type']} meals")
    print(f"\n{plan.get('explanation', '')}")
    if plan.get("legs"):
        print("\nShelter    Driver     Meals    Pickup    Delivery (Pacific)")
        for leg in plan["legs"]:
            print(f"{leg['shelter_id']:<11}{leg['driver_id']:<11}{leg['quantity']:<9}"
                  f"{clock(leg['pickup_at']):<10}{clock(leg['deliver_at'])}")
        planned = sum(leg["quantity"] for leg in plan["legs"])
        print(f"\nProposed: {planned}/{donation['quantity']} meals")
    reservations = [r for r in result["world"]["reservations"] if r["donation_id"] == donation["id"]]
    print(f"Reserved: {sum(r['quantity'] for r in reservations)} meals")
    if result.get("event_applied"):
        print("A driver cancellation was applied; this is the revised plan.")
    for error in result.get("errors", []):
        print(f"Validation: {error}")
    if result.get("__interrupt__"):
        print("\nPaused for coordinator approval. No reservations have been made for this proposal.")


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="SF Food Rescue Dispatch (fictional simulation)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scenarios")
    demo = sub.add_parser("demo")
    demo.add_argument("--planner", choices=["baseline", "agent"], default="baseline")
    demo.add_argument("--scenario", default="split_donation")
    resume = sub.add_parser("resume")
    resume.add_argument("thread_id")
    resume.add_argument("--decision", choices=["approve", "reject"], required=True)
    evaluate = sub.add_parser("eval")
    evaluate.add_argument("--planner", choices=["baseline", "agent"], default="baseline")
    evaluate.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    evaluate.add_argument("--langsmith", action="store_true")
    evaluate.add_argument("--repetitions", type=int, default=1)
    args = parser.parse_args()
    try:
        if args.command == "scenarios":
            for c in scenarios():
                print(f"{c['name']:24} {c['split']}")
            return
        if args.command == "eval":
            if args.repetitions < 1:
                parser.error("--repetitions must be positive")
            if args.langsmith:
                if args.split == "all":
                    parser.error("Use --split dev or --split test for LangSmith experiments.")
                run_langsmith(args.planner, args.split, args.repetitions)
            else:
                results = []
                for case in scenarios():
                    if args.split != "all" and case["split"] != args.split:
                        continue
                    for repetition in range(args.repetitions):
                        output = run_case(case["inputs"], args.planner)
                        results.append({"scenario": case["name"], "repetition": repetition,
                                        **score(case["inputs"], output, case["outputs"])})
                print(json.dumps(results, indent=2))
            return
        Path(".runs").mkdir(exist_ok=True)
        with SqliteSaver.from_conn_string(".runs/checkpoints.sqlite") as saver:
            graph = build_graph(saver)
            if args.command == "demo":
                case = next((c for c in scenarios() if c["name"] == args.scenario), None)
                if case is None:
                    parser.error("Unknown scenario; run 'python -m food_rescue scenarios'.")
                thread_id = str(uuid4())
                config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 80,
                          "metadata": trace_metadata()}
                result = graph.invoke(dict(case["inputs"], planner=args.planner, history=[]), config)
            else:
                thread_id = args.thread_id
                config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 80,
                          "metadata": trace_metadata()}
                snapshot = graph.get_state(config)
                if not snapshot.values:
                    parser.error("Unknown thread ID.")
                if not snapshot.next:
                    parser.error("This thread has finished; start a new demo.")
                result = graph.invoke(Command(resume={"approved": args.decision == "approve"}), config)
            display(result)
            print(f"\nThread: {thread_id}")
            if result.get("__interrupt__"):
                print(f"Resume: python -m food_rescue resume {thread_id} --decision approve")
    except (ValueError, RuntimeError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
