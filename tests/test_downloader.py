from hifi.downloader import (
    build_ydl_opts, extract_metadata, sanitize_filename,
    _parse_title_for_artist, _clean_title,
)


def test_build_ydl_opts_default():
    opts = build_ydl_opts(output_dir="/tmp/music", preferred_format="best")
    assert opts["format"] == "ba[acodec=opus]/ba[acodec=flac]/ba[acodec=vorbis]/ba/best"
    assert opts["paths"]["home"] == "/tmp/music"
    assert opts["postprocessors"][0]["key"] == "FFmpegExtractAudio"
    assert opts["postprocessors"][0]["preferredcodec"] == "opus"
    assert opts["postprocessors"][0]["preferredquality"] == "0"


def test_build_ydl_opts_specific_format():
    opts = build_ydl_opts(output_dir="/tmp", preferred_format="flac")
    assert opts["postprocessors"][0]["preferredcodec"] == "flac"


def test_build_ydl_opts_best_keeps_opus_default():
    opts = build_ydl_opts(output_dir="/tmp", preferred_format="best")
    assert opts["postprocessors"][0]["preferredcodec"] == "opus"


def test_extract_metadata_full():
    info = {
        "id": "abc123",
        "title": "Never Gonna Give You Up",
        "artist": "Rick Astley",
        "track": "Never Gonna Give You Up",
        "album": "Whenever You Need Somebody",
        "uploader": "RickAstleyVEVO",
        "webpage_url": "https://www.youtube.com/watch?v=abc123",
        "extractor_key": "Youtube",
    }
    meta = extract_metadata(info)
    assert meta["artist"] == "Rick Astley"
    assert meta["title"] == "Never Gonna Give You Up"
    assert meta["album"] == "Whenever You Need Somebody"
    assert meta["source"] == "Youtube"


def test_extract_metadata_fallback_to_uploader():
    info = {
        "id": "abc",
        "title": "Some Song",
        "uploader": "SomeChannel",
        "webpage_url": "https://www.youtube.com/watch?v=abc",
        "extractor_key": "Youtube",
    }
    meta = extract_metadata(info)
    assert meta["artist"] == "SomeChannel"
    assert meta["title"] == "Some Song"


def test_extract_metadata_track_over_title():
    info = {
        "id": "abc",
        "title": "Artist - Song (Official Video)",
        "track": "Song",
        "artist": "Artist",
        "extractor_key": "Youtube",
    }
    meta = extract_metadata(info)
    assert meta["title"] == "Song"


def test_extract_metadata_parses_artist_from_title():
    """The exact case that broke: game OST with artist in parens."""
    info = {
        "id": "kpnW68Q8ltc",
        "title": "Doom Eternal OST - The Only Thing They Fear Is You (Mick Gordon) [Doom Eternal Theme]",
        "uploader": "Revive Music",
        "extractor_key": "Youtube",
    }
    meta = extract_metadata(info)
    assert meta["artist"] == "Mick Gordon"
    assert meta["title"] == "The Only Thing They Fear Is You"


def test_extract_metadata_dash_artist_no_parens():
    info = {
        "id": "abc",
        "title": "Daft Punk - Around the World (Official Video)",
        "uploader": "SomeChannel",
        "extractor_key": "Youtube",
    }
    meta = extract_metadata(info)
    assert meta["artist"] == "Daft Punk"
    assert meta["title"] == "Around the World"


def test_extract_metadata_creator_fallback():
    info = {
        "id": "abc",
        "title": "Some Song",
        "creator": "Real Creator",
        "uploader": "SomeChannel",
        "extractor_key": "Youtube",
    }
    meta = extract_metadata(info)
    assert meta["artist"] == "Real Creator"


# --- Title parsing unit tests ---

def test_parse_title_artist_dash_title():
    artist, title = _parse_title_for_artist("Mick Gordon - The Only Thing They Fear Is You")
    assert artist == "Mick Gordon"
    assert title == "The Only Thing They Fear Is You"


def test_parse_title_game_ost_with_paren_artist():
    artist, title = _parse_title_for_artist(
        "Doom Eternal OST - The Only Thing They Fear Is You (Mick Gordon) [Doom Eternal Theme]"
    )
    assert artist == "Mick Gordon"
    assert title == "The Only Thing They Fear Is You"


def test_parse_title_official_video_stripped():
    artist, title = _parse_title_for_artist("Daft Punk - Around the World (Official Video)")
    assert artist == "Daft Punk"
    assert title == "Around the World"


def test_parse_title_no_pattern():
    artist, title = _parse_title_for_artist("Just A Simple Title")
    assert artist is None
    assert title == "Just A Simple Title"


def test_parse_title_paren_artist_no_dash():
    artist, title = _parse_title_for_artist("The Only Thing They Fear Is You (Mick Gordon)")
    assert artist == "Mick Gordon"
    assert title == "The Only Thing They Fear Is You"


def test_clean_title_strips_noise():
    assert _clean_title("Song Title Official Video HD") == "Song Title"
    assert _clean_title("Song [Official Audio]") == "Song"


def test_sanitize_filename():
    assert sanitize_filename('AC/DC - Back: In "Black"') == "AC_DC - Back_ In _Black_"


def test_sanitize_filename_strips_whitespace():
    assert sanitize_filename("  hello  ") == "hello"
