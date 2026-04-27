from hifi.searcher import (
    Candidate,
    _extract_remixer,
    rank_candidates,
    score_candidate,
)


def _c(title: str, uploader: str, duration: int = 400,
       view_count: int = 1000, video_id: str = "x") -> Candidate:
    return Candidate(
        video_id=video_id,
        title=title,
        uploader=uploader,
        duration=duration,
        view_count=view_count,
        url=f"https://www.youtube.com/watch?v={video_id}",
    )


def test_extract_remixer_basic():
    assert _extract_remixer("Satellite (Arkasia Remix)") == "Arkasia"
    assert _extract_remixer("Song Title (Seven Lions remix)") == "Seven Lions"
    assert _extract_remixer("Original Song Title") is None


def test_topic_channel_wins():
    artist = "Rick Astley"
    title = "Never Gonna Give You Up"
    cands = [
        _c("Never Gonna Give You Up", "Rick Astley - Topic", 214, 500_000, "a"),
        _c("Never Gonna Give You Up", "RandomUploader", 214, 2_000_000, "b"),
    ]
    ranked = rank_candidates(cands, artist, title, mb_duration=214)
    assert ranked[0].video_id == "a"


def test_remix_match_penalized():
    artist = "Oceanlab"
    title = "Satellite (Arkasia Remix)"
    cands = [
        _c("Oceanlab - Satellite (Seven Lions Remix)", "BigChannel",
           313, 2_000_000, "wrong"),
        _c("Oceanlab - Satellite (Arkasia Remix)", "SmallChannel",
           405, 5_000, "right"),
    ]
    ranked = rank_candidates(cands, artist, title)
    assert ranked[0].video_id == "right"


def test_live_version_penalized_when_not_in_query():
    artist = "Coldplay"
    title = "Yellow"
    cands = [
        _c("Coldplay - Yellow", "Coldplay - Topic", 269, 1_000, "studio"),
        _c("Coldplay - Yellow (Live at Glastonbury)", "Coldplay - Topic",
           280, 5_000_000, "live"),
    ]
    ranked = rank_candidates(cands, artist, title)
    assert ranked[0].video_id == "studio"


def test_official_uploader_boost():
    artist = "Oceanlab"
    title = "Satellite (Arkasia Remix)"
    cands = [
        _c("[Remastered] Oceanlab - Satellite (Arkasia remix)",
           "OfficialArkasia", 403, 41_000, "official"),
        _c("Oceanlab - Satellite (Arkasia Remix)",
           "Xtreme Music Fanmake", 413, 1_750, "fanmake"),
    ]
    ranked = rank_candidates(cands, artist, title)
    assert ranked[0].video_id == "official"


def test_fanmake_penalized():
    cands = [
        _c("Song Title Fanmake", "Uploader", 200, 10_000, "fan"),
        _c("Song Title", "Uploader", 200, 1_000, "real"),
    ]
    ranked = rank_candidates(cands, "", "Song Title")
    assert ranked[0].video_id == "real"


def test_duration_match_helps():
    cands = [
        _c("Song Title", "UploaderA", 300, 1_000, "a"),
        _c("Song Title", "UploaderB", 450, 1_000, "b"),
    ]
    ranked = rank_candidates(cands, "", "Song Title", mb_duration=305)
    assert ranked[0].video_id == "a"


def test_score_breakdown_populated():
    c = _c("Oceanlab - Satellite (Arkasia Remix)", "OfficialArkasia",
           405, 41_000)
    score_candidate(c, "Oceanlab", "Satellite (Arkasia Remix)", 405)
    assert "uploader" in c.score_breakdown
    assert "duration" in c.score_breakdown
    assert "title_sim" in c.score_breakdown
    assert c.score_breakdown["uploader"] > 0
    assert c.score_breakdown["duration"] == 2.0
