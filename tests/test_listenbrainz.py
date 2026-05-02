"""Unit tests for the LB API client (listenbrainz.py)."""

from hifi import listenbrainz


def test_metadata_recording_parses_artist_and_inline_tags(monkeypatch):
    fake_response = {
        "ec0da94e-fbfe-4eb0-968e-024d4c32d1d0": {
            "artist": {
                "name": "New Order",
                "artist_credit_id": 846,
                "artists": [
                    {"artist_mbid": "f1106b17-dcbb", "name": "New Order"},
                ],
            },
            "recording": {"name": "The Perfect Kiss", "length": 289960},
            "release": {"name": "Low-Life", "year": 1985},
            "tag": {
                "artist": [
                    {"artist_mbid": "f1106b17", "count": 12, "tag": "electronic"},
                    {"artist_mbid": "f1106b17", "count": 13, "tag": "post-punk"},
                    {"artist_mbid": "f1106b17", "count": 9, "tag": "alternative dance"},
                ],
            },
        },
    }

    captured: list[str] = []

    def fake_get(url: str):
        captured.append(url)
        return fake_response

    monkeypatch.setattr(listenbrainz, "_get_json", fake_get)

    out = listenbrainz.metadata_recording(["ec0da94e-fbfe-4eb0-968e-024d4c32d1d0"])

    assert "ec0da94e-fbfe-4eb0-968e-024d4c32d1d0" in out
    info = out["ec0da94e-fbfe-4eb0-968e-024d4c32d1d0"]
    assert info["artist_credit_name"] == "New Order"
    assert info["recording_name"] == "The Perfect Kiss"
    assert info["release_name"] == "Low-Life"
    assert info["length"] == 289960
    assert info["artist_mbids"] == ["f1106b17-dcbb"]
    assert info["inline_tags"] == {"electronic", "post-punk", "alternative dance"}
    # canonical_recording_mbid is intentionally None — Core API doesn't expose it.
    assert info["canonical_recording_mbid"] is None
    # URL form sanity.
    assert len(captured) == 1
    assert "metadata/recording/?recording_mbids=" in captured[0]
    assert "inc=artist+tag" in captured[0]


def test_metadata_recording_chunks_large_batches(monkeypatch):
    captured: list[str] = []

    def fake_get(url: str):
        captured.append(url)
        return {}

    monkeypatch.setattr(listenbrainz, "_get_json", fake_get)
    monkeypatch.setattr(listenbrainz, "LISTENBRAINZ_METADATA_CHUNK", 3)

    mbids = [f"mbid-{i:02d}" for i in range(7)]
    listenbrainz.metadata_recording(mbids)

    # 7 mbids in chunks of 3 -> 3 calls (3 + 3 + 1).
    assert len(captured) == 3
    assert "mbid-00,mbid-01,mbid-02" in captured[0]
    assert "mbid-06" in captured[2]


def test_metadata_recording_handles_empty_input():
    assert listenbrainz.metadata_recording([]) == {}


def test_metadata_recording_skips_malformed_entries(monkeypatch):
    monkeypatch.setattr(
        listenbrainz, "_get_json",
        lambda url: {
            "good-mbid": {
                "artist": {"name": "X", "artists": [{"artist_mbid": "a"}]},
                "tag": {"artist": [{"tag": "trance", "count": 5}]},
            },
            "bad-mbid": "not a dict",
        },
    )
    out = listenbrainz.metadata_recording(["good-mbid", "bad-mbid"])
    assert "good-mbid" in out
    assert "bad-mbid" not in out


def test_token_accepts_both_env_names(monkeypatch):
    monkeypatch.delenv("LISTENBRAINZ_TOKEN", raising=False)
    monkeypatch.delenv("LISTENBRAINZ_USER_TOKEN", raising=False)
    assert listenbrainz._token() is None

    monkeypatch.setenv("LISTENBRAINZ_TOKEN", "short-form")
    assert listenbrainz._token() == "short-form"

    monkeypatch.setenv("LISTENBRAINZ_USER_TOKEN", "long-form")
    # Both set: prefer the more-explicit USER_TOKEN.
    assert listenbrainz._token() == "long-form"
