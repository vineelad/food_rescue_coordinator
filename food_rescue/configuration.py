"""Application metadata shared by dispatch traces and evaluation experiments."""

import os


def trace_metadata() -> dict[str, str]:
    return {
        "agent_id": os.getenv("LANGSMITH_AGENT_ID", "vin"),
        "agent_environment": os.getenv("LANGSMITH_AGENT_ENVIRONMENT", "local"),
    }
