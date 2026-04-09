from hifi.cleaner import clean_url


def test_strip_youtube_si_param():
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=abc123"
    assert clean_url(url) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_strip_multiple_tracking_params():
    url = "https://www.youtube.com/watch?v=abc&si=x&feature=share&utm_source=twitter"
    assert clean_url(url) == "https://www.youtube.com/watch?v=abc"


def test_normalize_youtu_be():
    url = "https://youtu.be/dQw4w9WgXcQ?si=xyz"
    assert clean_url(url) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_normalize_music_youtube():
    url = "https://music.youtube.com/watch?v=abc123&feature=share"
    assert clean_url(url) == "https://www.youtube.com/watch?v=abc123"


def test_normalize_shorts():
    url = "https://youtube.com/shorts/abc123?feature=share"
    assert clean_url(url) == "https://www.youtube.com/watch?v=abc123"


def test_strip_fragment():
    url = "https://www.youtube.com/watch?v=abc#t=30"
    assert clean_url(url) == "https://www.youtube.com/watch?v=abc"


def test_non_youtube_strips_tracking():
    url = "https://soundcloud.com/artist/track?utm_source=twitter&ref=share"
    assert clean_url(url) == "https://soundcloud.com/artist/track"


def test_non_youtube_preserves_essential_params():
    url = "https://bandcamp.com/track?id=12345&utm_source=twitter"
    result = clean_url(url)
    assert "id=12345" in result
    assert "utm_source" not in result


def test_preserves_youtube_v_param():
    url = "https://www.youtube.com/watch?v=abc&list=PLxyz&index=3"
    result = clean_url(url)
    assert result == "https://www.youtube.com/watch?v=abc"


def test_handles_bare_url_no_params():
    url = "https://www.youtube.com/watch?v=abc"
    assert clean_url(url) == "https://www.youtube.com/watch?v=abc"


def test_strips_affiliate_params():
    url = "https://example.com/song?affiliate=XT&region=US"
    result = clean_url(url)
    assert "affiliate" not in result
    assert "region" not in result
