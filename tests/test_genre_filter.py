"""Unit tests for the seed-derived genre allowlist + post-filter."""

from collections import Counter

from hifi.recommender import (
    Pick,
    derive_genre_allowlist,
    filter_picks_by_genre,
    filter_picks_by_owned,
)
from hifi import recommender


def _stub_artist_tags(monkeypatch, mapping: dict[str, set[str]]):
    """Stub _artist_tags so tests don't hit MusicBrainz."""
    def fake(amb: str) -> set[str]:
        return mapping.get(amb, set())
    monkeypatch.setattr(recommender, "_artist_tags", fake)


def _meta(rec_to_artists: dict[str, list[str]]) -> dict[str, dict]:
    return {r: {"artist_mbids": ams} for r, ams in rec_to_artists.items()}


def test_allowlist_keeps_repeated_tags(monkeypatch):
    _stub_artist_tags(monkeypatch, {
        "art-1": {"dubstep", "edm"},
        "art-2": {"dubstep", "trance"},
        "art-3": {"trance", "ambient"},
    })
    meta = _meta({"rec-1": ["art-1"], "rec-2": ["art-2"], "rec-3": ["art-3"]})
    allow = derive_genre_allowlist(["rec-1", "rec-2", "rec-3"], meta)
    # dubstep (2) and trance (2) repeat; edm and ambient still make top-10
    assert "dubstep" in allow
    assert "trance" in allow
    # Singletons still admitted via top-10 when allowlist is small
    assert "edm" in allow
    assert "ambient" in allow


def test_allowlist_empty_when_no_tags(monkeypatch):
    _stub_artist_tags(monkeypatch, {"art-1": set()})
    meta = _meta({"rec-1": ["art-1"]})
    assert derive_genre_allowlist(["rec-1"], meta) == set()


def test_filter_keeps_matching_picks(monkeypatch):
    _stub_artist_tags(monkeypatch, {
        "edm-art": {"dubstep"},
        "pop-art": {"pop", "dance pop"},
    })
    picks = [
        Pick(artist="EdmA", title="X", mbid="rec-edm", score=10),
        Pick(artist="PopA", title="Y", mbid="rec-pop", score=20),
    ]
    meta = _meta({"rec-edm": ["edm-art"], "rec-pop": ["pop-art"]})
    out = filter_picks_by_genre(picks, {"dubstep", "trance"}, meta)
    assert [p.mbid for p in out] == ["rec-edm"]


def test_filter_lenient_keeps_untagged_when_no_deny(monkeypatch):
    """Untagged picks (after MB fallback) are kept when the caller is
    lenient AND has no denylist active."""
    _stub_artist_tags(monkeypatch, {"unknown-art": set()})
    monkeypatch.setattr(recommender.mb, "get_recording_by_id",
                        lambda *a, **k: {"recording": {}})
    picks = [Pick(artist="?", title="?", mbid="rec-unknown", score=1)]
    meta = _meta({"rec-unknown": ["unknown-art"]})
    out = filter_picks_by_genre(picks, {"dubstep"}, meta, exclude=set())
    assert out == picks


def test_filter_with_deny_drops_untagged(monkeypatch):
    """When a denylist is active the caller is explicit about exclusions,
    so unknowns are dropped — better to lose a niche pick than to risk
    pop slipping through on missing tags."""
    _stub_artist_tags(monkeypatch, {"unknown-art": set()})
    monkeypatch.setattr(recommender.mb, "get_recording_by_id",
                        lambda *a, **k: {"recording": {}})
    picks = [Pick(artist="?", title="?", mbid="rec-unknown", score=1)]
    meta = _meta({"rec-unknown": ["unknown-art"]})
    out = filter_picks_by_genre(picks, {"dubstep"}, meta, exclude={"pop"})
    assert out == []


def test_filter_strict_drops_untagged(monkeypatch):
    _stub_artist_tags(monkeypatch, {"unknown-art": set()})
    picks = [Pick(artist="?", title="?", mbid="rec-unknown", score=1)]
    meta = _meta({"rec-unknown": ["unknown-art"]})
    out = filter_picks_by_genre(picks, {"dubstep"}, meta, strict=True)
    assert out == []


def test_filter_no_allowlist_no_deny_passes_through(monkeypatch):
    _stub_artist_tags(monkeypatch, {})
    picks = [Pick(artist="X", title="Y", mbid="rec-1", score=5)]
    out = filter_picks_by_genre(
        picks, set(), _meta({"rec-1": []}), exclude=set(),
    )
    assert out == picks


def test_owned_filter_drops_by_mbid():
    picks = [
        Pick(artist="A", title="X", mbid="have-it", score=1),
        Pick(artist="A", title="Y", mbid="want-it", score=2),
    ]
    out = filter_picks_by_owned(picks, owned_mbids={"have-it"}, owned_titles=set())
    assert [p.mbid for p in out] == ["want-it"]


def test_owned_filter_drops_by_normalized_title():
    picks = [
        Pick(artist="Seven Lions", title="Polarized", mbid="m1", score=1),
        Pick(artist="MitiS", title="Born", mbid="m2", score=2),
    ]
    # User-supplied titles: case-folded, whitespace-stripped on both sides
    owned = {"seven lions|polarized"}
    out = filter_picks_by_owned(picks, owned_mbids=set(), owned_titles=owned)
    assert [p.mbid for p in out] == ["m2"]


def test_owned_filter_no_owned_passes_through():
    picks = [Pick(artist="A", title="T", mbid="m", score=1)]
    assert filter_picks_by_owned(picks, set(), set()) == picks
