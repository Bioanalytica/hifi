"""Unit tests for the genre-neighborhood expansion (genre_graph.py)."""

import time

from hifi import genre_graph


def _stub_lb(monkeypatch, mapping: dict[str, list[dict]]):
    """Stub listenbrainz.tag_similarity so tests don't hit the network."""
    calls: list[list[str]] = []

    def fake(tags: list[str]) -> dict[str, list[dict]]:
        calls.append(list(tags))
        return {t: list(mapping.get(t, [])) for t in tags}

    monkeypatch.setattr(genre_graph, "tag_similarity", fake)
    return calls


def _redirect_cache(monkeypatch, tmp_path):
    cache = tmp_path / "genre_graph.json"
    monkeypatch.setattr(genre_graph, "CACHE_PATH", str(cache))
    return cache


def test_tag_variants_handles_separators():
    assert genre_graph._tag_variants("future bass") == {"future bass", "future-bass"}
    assert genre_graph._tag_variants("future-bass") == {"future bass", "future-bass"}
    assert genre_graph._tag_variants("trance") == {"trance"}
    assert genre_graph._tag_variants("") == set()
    assert genre_graph._tag_variants("  Future Bass  ") == {"future bass", "future-bass"}


def test_expand_filters_by_min_count(monkeypatch, tmp_path):
    _redirect_cache(monkeypatch, tmp_path)
    _stub_lb(monkeypatch, {
        "future bass": [
            {"similar_tag": "trap", "count": 1538},
            {"similar_tag": "edm", "count": 1184},
            {"similar_tag": "vancouver", "count": 4},  # below default min_count=5
            {"similar_tag": "brony", "count": 2},
        ],
    })
    out = genre_graph.expand_genre("future bass", top_n=15, min_count=5)
    assert "trap" in out
    assert "edm" in out
    assert "vancouver" not in out
    assert "brony" not in out


def test_expand_applies_denylist(monkeypatch, tmp_path):
    _redirect_cache(monkeypatch, tmp_path)
    _stub_lb(monkeypatch, {
        "future bass": [
            {"similar_tag": "pop", "count": 500},  # in denylist
            {"similar_tag": "alternative pop", "count": 86},  # in denylist
            {"similar_tag": "melodic dubstep", "count": 200},  # OK
        ],
    })
    out = genre_graph.expand_genre("future bass", top_n=15, min_count=5)
    assert "pop" not in out
    assert "alternative pop" not in out
    assert "melodic dubstep" in out


def test_expand_includes_input_tag(monkeypatch, tmp_path):
    _redirect_cache(monkeypatch, tmp_path)
    _stub_lb(monkeypatch, {"future bass": []})  # LB returns nothing
    out = genre_graph.expand_genre("future bass")
    assert "future bass" in out
    # Variants too.
    assert "future-bass" in out


def test_expand_emits_separator_variants(monkeypatch, tmp_path):
    _redirect_cache(monkeypatch, tmp_path)
    _stub_lb(monkeypatch, {
        "future-bass": [
            # LB returns the spaced form, common in MB tag data.
            {"similar_tag": "melodic dubstep", "count": 100},
        ],
    })
    out = genre_graph.expand_genre("future-bass", min_count=5)
    # Both forms of the input.
    assert "future-bass" in out and "future bass" in out
    # Both forms of the neighbor.
    assert "melodic dubstep" in out and "melodic-dubstep" in out


def test_expand_top_n_cutoff(monkeypatch, tmp_path):
    _redirect_cache(monkeypatch, tmp_path)
    _stub_lb(monkeypatch, {
        "future bass": [
            {"similar_tag": f"neighbor {i}", "count": 100 - i}
            for i in range(20)
        ],
    })
    out = genre_graph.expand_genre("future bass", top_n=3, min_count=1)
    # Top 3 by count + variants of input + variants of each neighbor.
    # Input: future bass + future-bass = 2 entries
    # Each neighbor "neighbor N" has 2 variants (spaced + hyphenated).
    # So total: 2 + 3 * 2 = 8.
    assert len(out) == 8
    assert "neighbor 0" in out
    assert "neighbor 2" in out
    assert "neighbor 3" not in out


def test_expand_caches(monkeypatch, tmp_path):
    _redirect_cache(monkeypatch, tmp_path)
    calls = _stub_lb(monkeypatch, {
        "future bass": [{"similar_tag": "trap", "count": 100}],
    })
    a = genre_graph.expand_genre("future bass")
    b = genre_graph.expand_genre("future bass")
    assert a == b
    assert len(calls) == 1  # Second call hit cache.


def test_expand_cache_ttl_expires(monkeypatch, tmp_path):
    _redirect_cache(monkeypatch, tmp_path)
    calls = _stub_lb(monkeypatch, {
        "future bass": [{"similar_tag": "trap", "count": 100}],
    })
    genre_graph.expand_genre("future bass")
    # Backdate the cache entry past the TTL.
    cache = genre_graph._load_cache()
    assert cache, "cache should have entries"
    for k in cache:
        cache[k]["ts"] = time.time() - genre_graph.CACHE_TTL_SECONDS - 1
    genre_graph._save_cache(cache)

    genre_graph.expand_genre("future bass")
    assert len(calls) == 2  # Refetched after TTL.


def test_expand_genres_unions(monkeypatch, tmp_path):
    _redirect_cache(monkeypatch, tmp_path)
    _stub_lb(monkeypatch, {
        "future bass": [{"similar_tag": "trap", "count": 100}],
        "melodic dubstep": [{"similar_tag": "drumstep", "count": 100}],
    })
    out = genre_graph.expand_genres(["future bass", "melodic dubstep"])
    assert "trap" in out
    assert "drumstep" in out
    assert "future bass" in out
    assert "melodic dubstep" in out


def test_expand_normalizes_input(monkeypatch, tmp_path):
    _redirect_cache(monkeypatch, tmp_path)
    _stub_lb(monkeypatch, {
        "future bass": [{"similar_tag": "trap", "count": 100}],
    })
    out = genre_graph.expand_genre("  Future Bass  ")
    assert "future bass" in out
    assert "trap" in out
