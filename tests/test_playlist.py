import json

from hifi.library import read_seed_file
from hifi.playlist import PlaylistEntry, write, write_jspf, write_m3u


def _entries():
    return [
        PlaylistEntry(artist="Oceanlab", title="Satellite", mbid="abc-1"),
        PlaylistEntry(artist="Coldplay", title="Yellow", mbid="abc-2"),
    ]


def test_write_m3u_round_trips_through_seed_reader(tmp_path):
    p = tmp_path / "out.m3u"
    write_m3u(_entries(), str(p))
    seeds = read_seed_file(str(p))
    assert [(s.artist, s.title) for s in seeds] == [
        ("Oceanlab", "Satellite"),
        ("Coldplay", "Yellow"),
    ]


def test_write_jspf_produces_valid_json_with_tracks(tmp_path):
    p = tmp_path / "out.jspf"
    write_jspf(_entries(), str(p))
    doc = json.loads(p.read_text())
    assert "playlist" in doc
    tracks = doc["playlist"]["track"]
    assert len(tracks) == 2
    assert tracks[0]["creator"] == "Oceanlab"
    assert tracks[0]["title"] == "Satellite"
    assert tracks[0]["identifier"] == ["https://musicbrainz.org/recording/abc-1"]


def test_write_dispatches_by_extension(tmp_path):
    m3u = tmp_path / "out.m3u"
    jspf = tmp_path / "out.jspf"
    write(_entries(), str(m3u))
    write(_entries(), str(jspf))
    assert m3u.read_text().startswith("#EXTM3U")
    assert json.loads(jspf.read_text())["playlist"]["track"][0]["creator"] == "Oceanlab"
