import os

DEFAULT_OUTPUT_DIR = os.path.expanduser("~/Music")
DEFAULT_FORMAT = "best"
DB_PATH = os.path.expanduser("~/tools/hifi/hifi.db")
MUSICBRAINZ_CONFIDENCE_THRESHOLD = 80
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
