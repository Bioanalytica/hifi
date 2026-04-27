from hifi.library import Seed, read_seed_file


def test_read_seed_file_plain_text(tmp_path):
    p = tmp_path / "seeds.txt"
    p.write_text(
        "Oceanlab - Satellite\n"
        "# comment line\n"
        "\n"
        "Coldplay - Yellow\n"
        "no-dash-line\n"
    )
    seeds = read_seed_file(str(p))
    assert [(s.artist, s.title) for s in seeds] == [
        ("Oceanlab", "Satellite"),
        ("Coldplay", "Yellow"),
    ]


def test_read_seed_file_m3u_with_extinf(tmp_path):
    p = tmp_path / "playlist.m3u"
    p.write_text(
        "#EXTM3U\n"
        "#EXTINF:240,Oceanlab - Satellite\n"
        "/some/file/path.opus\n"
        "#EXTINF:-1,Rick Astley - Never Gonna Give You Up\n"
        "/another/path.opus\n"
    )
    seeds = read_seed_file(str(p))
    assert [(s.artist, s.title) for s in seeds] == [
        ("Oceanlab", "Satellite"),
        ("Rick Astley", "Never Gonna Give You Up"),
    ]


def test_read_seed_file_skips_blank_and_malformed(tmp_path):
    p = tmp_path / "messy.txt"
    p.write_text("\n\n   \nJustOneToken\nOk - Title\n")
    seeds = read_seed_file(str(p))
    assert len(seeds) == 1
    assert seeds[0].artist == "Ok"
    assert seeds[0].title == "Title"


def test_seed_defaults():
    s = Seed(artist="A", title="T")
    assert s.mbid is None
    assert s.source_path is None
