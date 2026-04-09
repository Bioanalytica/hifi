from hifi.downloader import build_ydl_opts, extract_metadata, sanitize_filename


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


def test_sanitize_filename():
    assert sanitize_filename('AC/DC - Back: In "Black"') == "AC_DC - Back_ In _Black_"


def test_sanitize_filename_strips_whitespace():
    assert sanitize_filename("  hello  ") == "hello"
