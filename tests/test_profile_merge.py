"""Tests for ``--profile NAME`` argv pre-pass and merge semantics."""

from hifi import cli, userconfig


def _redirect_config(monkeypatch, tmp_path):
    monkeypatch.setattr(userconfig, "_config_dir", lambda: str(tmp_path))


def test_extract_profile_name_handles_space_form():
    assert cli._extract_profile_name(["--profile", "rock"]) == "rock"


def test_extract_profile_name_handles_equals_form():
    assert cli._extract_profile_name(["--profile=rock"]) == "rock"


def test_extract_profile_name_returns_none_when_absent():
    assert cli._extract_profile_name(["--limit", "5"]) is None


def test_profile_supplies_argparse_defaults(monkeypatch, tmp_path):
    """Profile values become argparse defaults when --profile is set."""
    _redirect_config(monkeypatch, tmp_path)
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "rock.yml").write_text(
        "seed-file: /tmp/rock.m3u\n"
        "seed-sample: 25\n"
        "limit: 17\n"
        "exclude-genres:\n"
        "  - dance-pop\n"
    )
    args = cli.parse_recommend_args(["--profile", "rock"])
    assert args.profile == "rock"
    assert args.seed_file == "/tmp/rock.m3u"
    assert args.seed_sample == 25
    assert args.limit == 17
    assert args.exclude_genre == ["dance-pop"]


def test_cli_overrides_profile(monkeypatch, tmp_path):
    """CLI flag wins over the profile value."""
    _redirect_config(monkeypatch, tmp_path)
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "rock.yml").write_text(
        "limit: 17\n"
    )
    args = cli.parse_recommend_args(["--profile", "rock", "--limit", "5"])
    assert args.limit == 5


def test_profile_overrides_base_recommend_section(monkeypatch, tmp_path):
    """Profile value beats the base `recommend:` section."""
    _redirect_config(monkeypatch, tmp_path)
    (tmp_path / "config.yml").write_text(
        "recommend:\n"
        "  limit: 30\n"
    )
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "rock.yml").write_text(
        "limit: 17\n"
    )
    args = cli.parse_recommend_args(["--profile", "rock"])
    assert args.limit == 17


def test_no_profile_falls_back_to_base_recommend(monkeypatch, tmp_path):
    _redirect_config(monkeypatch, tmp_path)
    (tmp_path / "config.yml").write_text(
        "recommend:\n"
        "  limit: 42\n"
    )
    args = cli.parse_recommend_args([])
    assert args.profile is None
    assert args.limit == 42


def test_missing_profile_is_silent(monkeypatch, tmp_path):
    """Pointing at a non-existent profile returns empty defaults, not an error."""
    _redirect_config(monkeypatch, tmp_path)
    args = cli.parse_recommend_args(["--profile", "ghost"])
    assert args.profile == "ghost"
    # Falls through to compiled defaults.
    assert args.limit == 30  # RECOMMEND_LIMIT_DEFAULT
