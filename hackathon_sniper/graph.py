"""The LangGraph wiring.

load_context -> plan_queries -> (every source + web_search, in parallel) -> dedupe
-> one `verify` branch per event (fan-out via Send) -> rank -> sync_notion -> report.

`max_concurrency` caps how many branches (sources, verify) run at once; it is set from
`llm_concurrency` because the verify branches are where parallel LLM calls happen.
A NotionError from load_context propagates out of `run`: nothing is synced without
knowing the existing rows.
"""

from collections.abc import Callable
from typing import Any, Protocol, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from hackathon_sniper.deps import Deps
from hackathon_sniper.nodes.dedupe import dedupe
from hackathon_sniper.nodes.load_context import load_context
from hackathon_sniper.nodes.plan_queries import plan_queries
from hackathon_sniper.nodes.rank import rank
from hackathon_sniper.nodes.report import report
from hackathon_sniper.nodes.sources import make_source_node
from hackathon_sniper.nodes.sync_notion import sync_notion
from hackathon_sniper.nodes.verify import verify
from hackathon_sniper.nodes.web_search import web_search
from hackathon_sniper.state import GraphState, VerifyTask

# Counts supersteps, not branches: a run takes 8 (parallel branches share one), so 50 is
# plenty however many sources or verify branches there are.
RECURSION_LIMIT = 50

Graph = CompiledStateGraph[GraphState, None, GraphState, GraphState]


def route_verify(state: GraphState) -> list[Send] | str:
    """Fan out one parallel `verify` branch per event; skip straight to rank if there are none."""
    tasks = state.get("to_verify", [])
    return [Send("verify", VerifyTask(event=ev)) for ev in tasks] if tasks else "rank"


class Node[S](Protocol):
    """What StateGraph.add_node accepts: a callable whose parameter is named `state`."""

    def __call__(self, state: S) -> dict[str, Any]: ...


def _node[S](fn: Callable[[S], dict[str, Any]]) -> Node[S]:
    def node(state: S) -> dict[str, Any]:
        return fn(state)

    return node


def _bind[S](fn: Callable[[S, Deps], dict[str, Any]], deps: Deps) -> Node[S]:
    def node(state: S) -> dict[str, Any]:
        return fn(state, deps)

    return node


def build_graph(deps: Deps) -> Graph:
    graph = StateGraph(GraphState)
    graph.add_node("load_context", _bind(load_context, deps))
    graph.add_node("plan_queries", _bind(plan_queries, deps))
    branches: list[str] = []
    for name in deps.sources:
        graph.add_node(f"source_{name}", _node(make_source_node(name, deps)))
        branches.append(f"source_{name}")
    graph.add_node("web_search", _bind(web_search, deps))
    branches.append("web_search")
    graph.add_node("dedupe", _bind(dedupe, deps))
    graph.add_node("verify", _bind(verify, deps), input_schema=VerifyTask)
    graph.add_node("rank", _bind(rank, deps))
    graph.add_node("sync_notion", _bind(sync_notion, deps))
    graph.add_node("report", _bind(report, deps))

    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "plan_queries")
    for branch in branches:
        graph.add_edge("plan_queries", branch)
    graph.add_edge(branches, "dedupe")  # waits for every discovery branch
    graph.add_conditional_edges("dedupe", route_verify, ["verify", "rank"])
    graph.add_edge("verify", "rank")  # rank runs once, after every verify branch
    graph.add_edge("rank", "sync_notion")
    graph.add_edge("sync_notion", "report")
    graph.add_edge("report", END)
    return graph.compile()


def run(deps: Deps) -> GraphState:
    final = build_graph(deps).invoke(
        {"errors": []},
        config={
            "max_concurrency": deps.settings.llm_concurrency,
            "recursion_limit": RECURSION_LIMIT,
        },
    )
    return cast(GraphState, final)
