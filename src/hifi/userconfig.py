"""User config loader for hifi.

Reads ``~/.config/hifi/config.yml`` (XDG default; honors ``XDG_CONFIG_HOME``)
to provide defaults for CLI flags. The schema is loose by design — every key
is optional and falls back to the hardcoded default in ``hifi.config``.

Layout:

    # Defaults applied to the main ``hifi <url>`` mode.
    output: /mnt/intranet/Music
    format: best

    # Defaults applied to ``hifi recommend``.
    recommend:
      output: /mnt/intranet/Music/Recommended
      owned-dirs:
        - /mnt/intranet/Music
        - /mnt/c/Users/bioan/Music
      limit: 30
      seed-sample: 20
      genre-top-n: 15
      genre-min-count: 5
      lb-radio-mode: medium
      confirm: false   # auto-prompt after the picks table

CLI args always override scalar config values. Repeatable flags
(``--owned-dir``, ``--seed``, ``--genre``, ``--exclude-genre``,
``--seed-genre``) extend rather than replace, so config provides the
base list and CLI invocations add to it. To clear a list completely
in one run, edit / comment the config or pass ``--no-genre-filter``
etc. as appropriate.
"""

import logging
import os
from typing import Any

log = logging.getLogger(__name__)


def _config_dir() -> str:
    return os.path.join(
        os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
        "hifi",
    )


def config_path() -> str:
    return os.path.join(_config_dir(), "config.yml")


def state_path() -> str:
    """Where mutable runtime state (cached LB username, etc.) lives."""
    return os.path.join(_config_dir(), "state.json")


def profiles_dir() -> str:
    """Directory where named profile YAMLs live (``<name>.yml``)."""
    return os.path.join(_config_dir(), "profiles")


def load() -> dict:
    """Read and parse the config file. Returns ``{}`` when absent."""
    path = config_path()
    if not os.path.exists(path):
        return {}
    try:
        import yaml  # imported lazily so missing pyyaml doesn't break tests
    except ImportError:
        log.warning("pyyaml not installed; ignoring %s", path)
        return {}
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as e:
        log.warning("failed to parse %s: %s", path, e)
        return {}
    return data or {}


def load_profile(name: str) -> dict:
    """Read a named profile YAML from ``<profiles_dir>/<name>.yml``.

    Returns ``{}`` when the file is missing, malformed, or pyyaml is
    not installed. Same loose-failure semantics as :func:`load`. The
    returned dict is shaped like the ``recommend:`` section of the main
    config and gets merged on top of it; any keys not understood by
    argparse defaults are simply ignored.
    """
    if not name:
        return {}
    path = os.path.join(profiles_dir(), f"{name}.yml")
    if not os.path.exists(path):
        return {}
    try:
        import yaml
    except ImportError:
        log.warning("pyyaml not installed; ignoring %s", path)
        return {}
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as e:
        log.warning("failed to parse %s: %s", path, e)
        return {}
    if not isinstance(data, dict):
        log.warning("profile %s is not a mapping; ignoring", path)
        return {}
    return data


def section(name: str) -> dict:
    """Return one section of the config (e.g. ``recommend``)."""
    data = load()
    sub = data.get(name) or {}
    if not isinstance(sub, dict):
        log.warning("config section %r is not a mapping; ignoring", name)
        return {}
    return sub


def get(key: str, default: Any = None, *, in_section: str | None = None):
    """Fetch a single key from the top level or a named section."""
    if in_section:
        return section(in_section).get(key, default)
    return load().get(key, default)


def load_state() -> dict:
    """Read the runtime state file. Returns ``{}`` when absent or unreadable."""
    import json
    path = state_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f) or {}
    except (OSError, json.JSONDecodeError) as e:
        log.warning("failed to read %s: %s", path, e)
        return {}


def save_state(data: dict) -> None:
    """Write the runtime state file. Creates the dir if needed."""
    import json
    path = state_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        log.warning("failed to write %s: %s", path, e)
