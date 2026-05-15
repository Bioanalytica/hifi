import base64
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from mutagen.flac import Picture

from hifi.tagger import (
    embed_tags,
    fetch_cover_art,
    pick_canonical_release,
    search_musicbrainz,
    tag_file,
)


def _rel(rel_id, title, *, date="", status="Official",
         ptype="Album", secondary=None, rg_id=None):
    return {
        "id": rel_id,
        "title": title,
        "date": date,
        "status": status,
        "release-group": {
            "id": rg_id or f"rg-{rel_id}",
            "primary-type": ptype,
            "secondary-type-list": list(secondary or []),
        },
    }


def test_pick_canonical_returns_none_when_no_releases():
    assert pick_canonical_release({}) is None
    assert pick_canonical_release({"release-list": []}) is None


def test_pick_canonical_prefers_official_album_no_secondary():
    recording = {"release-list": [
        _rel("r-bootleg", "Live USA 2003", status="Bootleg",
             secondary=["Live"]),
        _rel("r-comp", "100 Greatest Rock", date="2017-11-10",
             secondary=["Compilation"]),
        _rel("r-real", "The Sickness", date="2000-09-12"),  # winner
        _rel("r-remaster", "The Sickness", date="2010-03-23"),
    ]}
    pick = pick_canonical_release(recording)
    assert pick is not None
    assert pick["id"] == "r-real"
    assert pick["title"] == "The Sickness"


def test_pick_canonical_picks_earliest_within_tier():
    """When multiple clean Tier-1 releases exist, take the earliest date."""
    recording = {"release-list": [
        _rel("r-remaster", "The Sickness", date="2010-03-23"),
        _rel("r-orig", "The Sickness", date="2000-09-12"),
        _rel("r-eu", "The Sickness", date="2002-09-16"),
    ]}
    pick = pick_canonical_release(recording)
    assert pick["id"] == "r-orig"


def test_pick_canonical_falls_through_to_tier2_for_soundtracks():
    """When only Compilation/Soundtrack Official-Album releases exist
    (track was never on a studio album), Tier-2 keeps them."""
    recording = {"release-list": [
        _rel("r-st", "Some Soundtrack", date="2015",
             secondary=["Soundtrack"]),
        _rel("r-comp", "Some Compilation", date="2010",
             secondary=["Compilation"]),
        _rel("r-live", "Some Live Album", date="2009",
             secondary=["Live"]),  # disqualified at Tier-2
        _rel("r-demo", "Some Demo", date="2008",
             secondary=["Demo"]),  # disqualified at Tier-2
    ]}
    pick = pick_canonical_release(recording)
    # Tier-1 empty (all have excluded secondaries). Tier-2 allows
    # Compilation and Soundtrack but rejects Live/Demo. Earliest of
    # those two wins -> "Some Compilation" 2010.
    assert pick["id"] == "r-comp"


def test_pick_canonical_falls_through_to_any_official():
    """When no Album-type Official release exists, accept any Official."""
    recording = {"release-list": [
        _rel("r-boot", "Bootleg", status="Bootleg"),
        _rel("r-single", "The Single", ptype="Single", date="1999"),
    ]}
    pick = pick_canonical_release(recording)
    assert pick["id"] == "r-single"


def test_pick_canonical_final_fallback_picks_something():
    """Even when nothing is Official, we still pick something so the
    download path has at least an album title."""
    recording = {"release-list": [
        _rel("r-1", "Demo Tape 2002", status="Bootleg", date="2002"),
        _rel("r-2", "Other Bootleg", status="Bootleg", date="2001"),
    ]}
    pick = pick_canonical_release(recording)
    # Earliest of the bootleg pool.
    assert pick["id"] == "r-2"


def test_pick_canonical_undated_loses_to_dated_in_same_tier():
    recording = {"release-list": [
        _rel("r-undated", "Mystery Album"),  # no date
        _rel("r-dated", "Real Album", date="2005"),
    ]}
    pick = pick_canonical_release(recording)
    assert pick["id"] == "r-dated"


@patch("hifi.tagger.mb")
def test_search_musicbrainz_good_match(mock_mb):
    mock_mb.search_recordings.return_value = {
        "recording-list": [
            {
                "id": "rec-1",
                "title": "Never Gonna Give You Up",
                "artist-credit": [{"artist": {"name": "Rick Astley"}}],
                "release-list": [
                    {
                        "id": "rel-1",
                        "title": "Whenever You Need Somebody",
                        "date": "1987",
                        "status": "Official",
                        "release-group": {
                            "id": "rg-1",
                            "primary-type": "Album",
                            "secondary-type-list": [],
                        },
                    }
                ],
                "ext:score": "100",
            }
        ]
    }

    result = search_musicbrainz("Rick Astley", "Never Gonna Give You Up")
    assert result is not None
    assert result["recording_id"] == "rec-1"
    assert result["artist"] == "Rick Astley"
    assert result["title"] == "Never Gonna Give You Up"
    assert result["album"] == "Whenever You Need Somebody"
    assert result["year"] == "1987"
    assert result["release_id"] == "rel-1"
    assert result["release_group_id"] == "rg-1"


@patch("hifi.tagger.mb")
def test_search_musicbrainz_skips_compilation(mock_mb):
    """Regression for the PowerAmp-bad-album problem: the picker must
    skip 'Greatest Hits 2017' style compilations and surface the
    earliest Official+Album+no-secondary release as the album."""
    mock_mb.search_recordings.return_value = {
        "recording-list": [
            {
                "id": "rec-1",
                "title": "Down With the Sickness",
                "artist-credit": [{"artist": {"name": "Disturbed"}}],
                "ext:score": "100",
                "release-list": [
                    # Compilation (the kind currently leaking through).
                    {"id": "r-comp", "title": "100 Greatest Rock",
                     "date": "2017-11-10", "status": "Official",
                     "release-group": {"id": "rg-comp", "primary-type": "Album",
                                       "secondary-type-list": ["Compilation"]}},
                    # Bootleg live recording.
                    {"id": "r-live", "title": "Live USA 2003",
                     "date": "2003", "status": "Bootleg",
                     "release-group": {"id": "rg-live", "primary-type": "Album",
                                       "secondary-type-list": ["Live"]}},
                    # The actual studio album.
                    {"id": "r-orig", "title": "The Sickness",
                     "date": "2000-09-12", "status": "Official",
                     "release-group": {"id": "rg-orig", "primary-type": "Album",
                                       "secondary-type-list": []}},
                ],
            }
        ]
    }
    result = search_musicbrainz("Disturbed", "Down With the Sickness")
    assert result["album"] == "The Sickness"
    assert result["year"] == "2000"
    assert result["release_id"] == "r-orig"
    assert result["release_group_id"] == "rg-orig"


@patch("hifi.tagger.mb")
def test_search_musicbrainz_low_score_returns_none(mock_mb):
    mock_mb.search_recordings.return_value = {
        "recording-list": [
            {
                "id": "rec-1",
                "title": "Wrong Song",
                "artist-credit": [{"artist": {"name": "Wrong Artist"}}],
                "release-list": [],
                "ext:score": "30",
            }
        ]
    }

    result = search_musicbrainz("Rick Astley", "Never Gonna Give You Up")
    assert result is None


@patch("hifi.tagger.mb")
def test_search_musicbrainz_no_results(mock_mb):
    mock_mb.search_recordings.return_value = {"recording-list": []}
    result = search_musicbrainz("Unknown", "Unknown")
    assert result is None


@patch("hifi.tagger.urllib.request.urlopen")
def test_fetch_cover_art_success(mock_urlopen):
    mock_response = MagicMock()
    mock_response.read.return_value = b"\x89PNG fake image data"
    mock_response.status = 200
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_urlopen.return_value = mock_response

    data = fetch_cover_art("release-123")
    assert data == b"\x89PNG fake image data"


@patch("hifi.tagger.urllib.request.urlopen")
def test_fetch_cover_art_404_returns_none(mock_urlopen):
    from urllib.error import HTTPError
    mock_urlopen.side_effect = HTTPError(
        url="http://example.com", code=404, msg="Not Found",
        hdrs=None, fp=None
    )
    data = fetch_cover_art("release-123")
    assert data is None


def test_embed_tags_opus(tmp_path):
    opus_path = str(tmp_path / "test.opus")
    os.system(
        f'ffmpeg -y -f lavfi -i anullsrc=r=48000:cl=mono -t 0.1 '
        f'-c:a libopus "{opus_path}" 2>/dev/null'
    )
    if not os.path.exists(opus_path):
        pytest.skip("ffmpeg not available or cannot create opus")

    embed_tags(opus_path, title="Test Song", artist="Test Artist",
               album="Test Album", year="2024")

    from mutagen.oggopus import OggOpus
    audio = OggOpus(opus_path)
    assert audio["title"] == ["Test Song"]
    assert audio["artist"] == ["Test Artist"]
    assert audio["album"] == ["Test Album"]
    assert audio["date"] == ["2024"]


def test_embed_tags_m4a(tmp_path):
    m4a_path = str(tmp_path / "test.m4a")
    os.system(
        f'ffmpeg -y -f lavfi -i anullsrc=r=44100:cl=mono -t 0.1 '
        f'-c:a aac "{m4a_path}" 2>/dev/null'
    )
    if not os.path.exists(m4a_path):
        pytest.skip("ffmpeg not available or cannot create m4a")

    embed_tags(m4a_path, title="Test Song", artist="Test Artist",
               album="Test Album", year="2024")

    from mutagen.mp4 import MP4
    audio = MP4(m4a_path)
    assert audio["\xa9nam"] == ["Test Song"]
    assert audio["\xa9ART"] == ["Test Artist"]
    assert audio["\xa9alb"] == ["Test Album"]
    assert audio["\xa9day"] == ["2024"]
