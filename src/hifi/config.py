import os

DEFAULT_OUTPUT_DIR = "/mnt/intranet/Music"
DEFAULT_FORMAT = "best"
DB_PATH = os.path.expanduser("~/tools/hifi/hifi.db")
MUSICBRAINZ_CONFIDENCE_THRESHOLD = 95
# Min token-set similarity (rapidfuzz, 0-100) between the search query
# "Artist Title" and MB's top result. Below this we treat MB as a miss
# and fall back to the original query for tagging.
MUSICBRAINZ_QUERY_SIMILARITY = 75
COVER_ART_SIZE = 500
PREFERRED_CODECS = ("opus", "flac", "vorbis")

TRACKING_PARAMS = frozenset({
    "si", "feature", "list", "index", "t", "pp",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "affiliate", "ref", "fbclid", "gclid", "region",
})

FILENAME_UNSAFE = str.maketrans({
    "/": "_", "\\": "_", ":": "_", "*": "_",
    "?": "_", '"': "_", "<": "_", ">": "_", "|": "_",
})

LISTENBRAINZ_LABS_BASE = "https://labs.api.listenbrainz.org"
LISTENBRAINZ_BASE = "https://api.listenbrainz.org/1"
LISTENBRAINZ_DEFAULT_ALGORITHM = (
    "session_based_days_7500_session_300_contribution_5"
    "_threshold_15_limit_50_skip_30"
)
SEED_SAMPLE_DEFAULT = 10
RECOMMEND_LIMIT_DEFAULT = 30
AUDIO_EXTENSIONS = (".opus", ".flac", ".m4a", ".mp3", ".ogg")
