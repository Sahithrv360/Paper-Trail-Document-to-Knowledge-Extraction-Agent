"""Safe schema summaries for prompt generation and query planning."""

from kg_agent.models import GraphSchema


def introspect_schema(store) -> GraphSchema:
    """Return the backend's validated schema summary."""
    return store.schema()
