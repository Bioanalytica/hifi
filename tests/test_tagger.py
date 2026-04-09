import base64
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from mutagen.flac import Picture

from hifi.tagger import (
    search_musicbrainz,
    fetch_cover_art,
    embed_tags,
    tag_file,
)


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
