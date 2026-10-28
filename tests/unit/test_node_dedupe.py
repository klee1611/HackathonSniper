from datetime import date, timedelta

from hackathon_sniper.models import Format, HackathonEvent, Lifecycle, Origin, Verification
from hackathon_sniper.nodes.dedupe import MAX_THEMES, dedupe, merge
from tests.factories import TODAY, event, make_deps


def test_stronger_origin_wins_but_never_blanks() -> None:
    base = event(
        prize_usd=1000,
        registration_deadline=date(2026, 11, 1),
        origin=Origin.SOURCE,
        sources=["devpost"],
    )
    new = event(prize_usd=5000, origin=Origin.PAGE, sources=["web"], format=Format.ONLINE)
    m = merge(base, new)
    assert (m.prize_usd, m.registration_deadline, m.format) == (
        5000,
        date(2026, 11, 1),
        Format.ONLINE,
    )
    assert (m.sources, m.origin) == (["devpost", "web"], Origin.PAGE)


def test_weaker_origin_only_fills_gaps() -> None:
    m = merge(
        event(prize_usd=1000, origin=Origin.PAGE),
        event(prize_usd=9999, location="Taipei", origin=Origin.SNIPPET),
    )
    assert (m.prize_usd, m.location) == (1000, "Taipei")


def test_merge_keeps_notion_page_id() -> None:
    assert merge(event(notion_page_id="p1"), event()).notion_page_id == "p1"


def test_merge_keeps_notion_owned_fields_against_fresh_source_candidate() -> None:
    row = event(
        notion_page_id="p1",
        fit_score=8,
        fit_reason="Matches AI interest",
        lifecycle=Lifecycle.EXPIRED,
        origin=Origin.SNIPPET,
    )
    m = merge(row, event(origin=Origin.SOURCE, sources=["devpost"], prize_usd=500))
    assert (m.fit_score, m.fit_reason, m.lifecycle, m.notion_page_id) == (
        8,
        "Matches AI interest",
        Lifecycle.EXPIRED,
        "p1",
    )
    assert m.prize_usd == 500


def test_source_description_survives_weaker_snippet_and_empty_page() -> None:
    mlh = event(origin=Origin.SOURCE, description="MLH diversity focus: Women")
    snippet = event(origin=Origin.SNIPPET, description="A hackathon from a search snippet")
    assert merge(mlh, snippet).description == "MLH diversity focus: Women"
    assert merge(snippet, mlh).description == "MLH diversity focus: Women"
    assert merge(mlh, event(origin=Origin.PAGE, description="")).description == (
        "MLH diversity focus: Women"
    )


def test_themes_dedupe_case_insensitively_sorted_and_capped() -> None:
    m = merge(event(themes=["ai", "Web3"]), event(themes=["AI", "Climate", " web3 "]))
    assert [t.casefold() for t in m.themes] == ["ai", "climate", "web3"]

    many = event(themes=[f"t{i:02d}" for i in range(MAX_THEMES)])
    grown = merge(many, event(themes=["extra1", "extra2"]))
    assert len(grown.themes) == MAX_THEMES
    assert len(merge(grown, event(themes=["extra3", "extra4"])).themes) == MAX_THEMES
    # A weaker view cannot displace the stronger side's themes once the list is full.
    full = event(themes=[f"t{i:02d}" for i in range(MAX_THEMES)], origin=Origin.PAGE)
    assert merge(full, event(themes=["aaa"], origin=Origin.SNIPPET)).themes == full.themes


def test_sources_are_sorted_and_deduped() -> None:
    m = merge(event(sources=["web", "devpost"]), event(sources=["devpost", "mlh"]))
    assert m.sources == ["devpost", "mlh", "web"]


def test_takes_newer_verification_only() -> None:
    old = event(verification=Verification.VERIFIED, verified_at=TODAY)
    stale = event(verification=Verification.PARTIAL, verified_at=TODAY - timedelta(days=3))
    assert merge(old, stale).verification == Verification.VERIFIED
    assert merge(stale, old).verified_at == TODAY


def test_merges_duplicates_and_splits_verify_queue() -> None:
    fresh = event(
        url="https://fresh.dev",
        name="Fresh",
        verified_at=TODAY,
        notion_page_id="p1",
        origin=Origin.PAGE,
        verification=Verification.VERIFIED,
    )
    stale = event(
        url="https://stale.dev",
        name="Stale",
        verified_at=TODAY - timedelta(days=30),
        notion_page_id="p2",
        registration_deadline=TODAY + timedelta(days=5),
    )
    expired = event(
        url="https://old.dev",
        name="Old",
        registration_deadline=TODAY - timedelta(days=1),
        notion_page_id="p3",
    )
    state = {
        "existing": {e.canonical_url: e for e in (fresh, stale, expired)},
        "candidates": [
            event(url="https://new.dev/?ref=x", name="New", sources=["devpost"]),
            event(url="https://new.dev", name="New", sources=["web"]),
            event(url="https://fresh.dev", name="Fresh", sources=["mlh"]),
        ],
    }
    out = dedupe(state, make_deps())

    assert [e.canonical_url for e in out["to_verify"]] == ["https://new.dev", "https://stale.dev"]
    new = out["to_verify"][0]
    assert (new.sources, new.last_seen) == (["devpost", "web"], TODAY)
    passthrough = {e.canonical_url: e for e in out["passthrough"]}
    assert set(passthrough) == {"https://fresh.dev", "https://old.dev"}
    assert (
        passthrough["https://fresh.dev"].sources,
        passthrough["https://fresh.dev"].last_seen,
    ) == (
        ["mlh"],
        TODAY,
    )


def test_expired_lifecycle_rows_not_seen_today_are_dropped() -> None:
    archived = event(
        url="https://archived.dev",
        lifecycle=Lifecycle.EXPIRED,
        registration_deadline=TODAY - timedelta(days=20),
        notion_page_id="p9",
    )
    out = dedupe({"existing": {archived.canonical_url: archived}, "candidates": []}, make_deps())
    assert (out["to_verify"], out["passthrough"]) == ([], [])


def test_caps_verification_and_carries_overflow() -> None:
    candidates = [event(url=f"https://e{i}.dev", name=f"E{i}") for i in range(5)]
    out = dedupe({"existing": {}, "candidates": candidates}, make_deps(max_verify=2))
    assert (len(out["to_verify"]), len(out["passthrough"])) == (2, 3)


def test_verify_order_is_deterministic_regardless_of_input_order() -> None:
    deadline = TODAY + timedelta(days=3)
    evs = [event(url=f"https://{c}.dev", name=c, registration_deadline=deadline) for c in "dbca"]
    for order in (evs, list(reversed(evs))):
        out = dedupe({"existing": {}, "candidates": order}, make_deps(max_verify=2))
        assert [e.canonical_url for e in out["to_verify"]] == ["https://a.dev", "https://b.dev"]
        assert [e.canonical_url for e in out["passthrough"]] == [
            "https://c.dev",
            "https://d.dev",
        ]


def test_extended_deadline_on_verified_row_is_reverified() -> None:
    row = event(
        url="https://ext.dev",
        notion_page_id="p1",
        origin=Origin.PAGE,
        verification=Verification.VERIFIED,
        verified_at=TODAY - timedelta(days=1),
        registration_deadline=TODAY - timedelta(days=1),
    )
    candidate = event(
        url="https://ext.dev",
        origin=Origin.SOURCE,
        sources=["devpost"],
        registration_deadline=TODAY + timedelta(days=10),
    )
    out = dedupe({"existing": {row.canonical_url: row}, "candidates": [candidate]}, make_deps())
    assert [e.canonical_url for e in out["to_verify"]] == ["https://ext.dev"]
    assert out["passthrough"] == []


def test_same_origin_candidates_do_not_churn_full_theme_list() -> None:
    stored = [f"t{i:02d}" for i in range(MAX_THEMES)]
    row = event(notion_page_id="p1", origin=Origin.SOURCE, themes=stored)
    day1 = merge(row, event(origin=Origin.SOURCE, themes=["aaa", "bbb"]))
    day2 = merge(day1, event(origin=Origin.SOURCE, themes=["ccc"]))
    assert day1.themes == stored
    assert day2.themes == stored


def test_candidate_folding_is_order_independent() -> None:
    devpost = event(name="From Devpost", origin=Origin.SOURCE, sources=["devpost"], prize_usd=100)
    web = event(name="From Web", origin=Origin.SOURCE, sources=["web"], prize_usd=200)
    outs = [
        dedupe({"existing": {}, "candidates": order}, make_deps())["to_verify"]
        for order in ([devpost, web], [web, devpost])
    ]
    assert outs[0] == outs[1]
    assert len(outs[0]) == 1


def test_reverify_order_prefers_closing_soon_then_stalest() -> None:
    def row(name: str, closes_in: int, verified_ago: int) -> HackathonEvent:
        return event(
            url=f"https://{name}.dev",
            name=name,
            notion_page_id=name,
            registration_deadline=TODAY + timedelta(days=closes_in),
            verified_at=TODAY - timedelta(days=verified_ago),
        )

    rows = [row("fresher", 100, 8), row("stalest", 100, 30), row("soon", 3, 8)]
    existing = {r.canonical_url: r for r in rows}
    out = dedupe({"existing": existing, "candidates": []}, make_deps())
    assert [e.canonical_url for e in out["to_verify"]] == [
        "https://soon.dev",
        "https://stalest.dev",
        "https://fresher.dev",
    ]
