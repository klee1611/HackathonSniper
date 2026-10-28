from hackathon_sniper.models import HackathonEvent
from hackathon_sniper.nodes.sources import make_source_node
from tests.factories import event, make_deps


def test_source_node_returns_candidates() -> None:
    ev = event()
    out = make_source_node("devpost", make_deps(sources={"devpost": lambda: [ev]}))({})
    assert out == {"candidates": [ev]}


def test_source_node_isolates_failures() -> None:
    def broken() -> list[HackathonEvent]:
        raise RuntimeError("HTML changed")

    out = make_source_node("mlh", make_deps(sources={"mlh": broken}))({})
    assert out == {"source_failures": ["mlh"], "errors": ["source mlh: HTML changed"]}


def test_source_node_isolates_missing_source() -> None:
    out = make_source_node("devfolio", make_deps(sources={}))({})
    assert out["source_failures"] == ["devfolio"]
    assert len(out["errors"]) == 1
