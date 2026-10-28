import logging
from collections.abc import Callable
from typing import Any

from hackathon_sniper.deps import Deps
from hackathon_sniper.state import GraphState

logger = logging.getLogger(__name__)


def make_source_node(name: str, deps: Deps) -> Callable[[GraphState], dict[str, Any]]:
    def node(state: GraphState) -> dict[str, Any]:
        try:
            events = deps.sources[name]()
        except Exception as e:  # isolation: one broken source must never stop the run
            logger.warning("source %s failed: %s: %s", name, type(e).__name__, e)
            return {"source_failures": [name], "errors": [f"source {name}: {e}"]}
        logger.info("source %s: %d events", name, len(events))
        return {"candidates": events}

    return node
