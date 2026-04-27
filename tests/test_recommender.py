from hifi.listenbrainz import SimilarRec
from hifi.recommender import Pick, aggregate_picks


def _s(seed: str, mbid: str, score: float, artist: str = "A", title: str = "T") -> SimilarRec:
    return SimilarRec(
        seed_mbid=seed,
        recording_mbid=mbid,
        score=score,
        artist_credit_name=artist,
        recording_name=title,
    )


def test_aggregate_sums_scores_across_seeds():
    sims = [
        _s("seed-1", "rec-X", 1.0, "A", "X"),
        _s("seed-2", "rec-X", 0.5, "A", "X"),
        _s("seed-1", "rec-Y", 0.9, "B", "Y"),
    ]
    out = aggregate_picks(sims, drop_mbids=set())
    assert out["rec-X"].score == 1.5
    assert out["rec-X"].seed_count == 2
    assert set(out["rec-X"].seeds) == {"seed-1", "seed-2"}
    assert out["rec-Y"].seed_count == 1


def test_drop_mbids_filters_out_already_have():
    sims = [
        _s("seed", "rec-keep", 1.0),
        _s("seed", "rec-already-have", 2.0),
    ]
    out = aggregate_picks(sims, drop_mbids={"rec-already-have"})
    assert "rec-keep" in out
    assert "rec-already-have" not in out


def test_drop_mbids_filters_seeds_themselves():
    sims = [
        _s("seed-A", "seed-A", 5.0),  # the seed surfaced as its own similar
        _s("seed-A", "rec-real", 1.0),
    ]
    out = aggregate_picks(sims, drop_mbids={"seed-A"})
    assert "seed-A" not in out
    assert "rec-real" in out


def test_aggregate_handles_empty_input():
    assert aggregate_picks([], drop_mbids=set()) == {}


def test_pick_field_defaults():
    p = Pick(artist="A", title="T", mbid="m", score=1.0)
    assert p.seed_count == 1
    assert p.seeds == []
