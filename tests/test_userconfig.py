"""Unit tests for the YAML user-config loader."""

import json

from hifi import userconfig


def _redirect_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(userconfig, "_config_dir",
                        lambda: str(tmp_path))


def test_load_returns_empty_when_missing(monkeypatch, tmp_path):
    _redirect_paths(monkeypatch, tmp_path)
    assert userconfig.load() == {}


def test_load_parses_yaml(monkeypatch, tmp_path):
    _redirect_paths(monkeypatch, tmp_path)
    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(
        "output: /tmp/x\n"
        "recommend:\n"
        "  limit: 50\n"
        "  owned-dirs:\n"
        "    - /a\n"
        "    - /b\n"
    )
    data = userconfig.load()
    assert data["output"] == "/tmp/x"
    assert data["recommend"]["limit"] == 50
    assert data["recommend"]["owned-dirs"] == ["/a", "/b"]


def test_section_returns_empty_for_missing(monkeypatch, tmp_path):
    _redirect_paths(monkeypatch, tmp_path)
    (tmp_path / "config.yml").write_text("output: /tmp/x\n")
    assert userconfig.section("recommend") == {}


def test_section_handles_non_mapping(monkeypatch, tmp_path):
    """A scalar in place of a section should be ignored, not crash."""
    _redirect_paths(monkeypatch, tmp_path)
    (tmp_path / "config.yml").write_text("recommend: not_a_dict\n")
    assert userconfig.section("recommend") == {}


def test_load_handles_invalid_yaml(monkeypatch, tmp_path):
    _redirect_paths(monkeypatch, tmp_path)
    (tmp_path / "config.yml").write_text("output: [unclosed\n")
    # Should warn but not raise.
    assert userconfig.load() == {}


def test_state_roundtrips(monkeypatch, tmp_path):
    _redirect_paths(monkeypatch, tmp_path)
    assert userconfig.load_state() == {}

    userconfig.save_state({"lb_user_name": "Skeptical"})
    assert userconfig.load_state() == {"lb_user_name": "Skeptical"}

    # State file is a sibling of config.yml.
    state_file = tmp_path / "state.json"
    assert json.loads(state_file.read_text()) == {"lb_user_name": "Skeptical"}


def test_state_handles_corrupt_file(monkeypatch, tmp_path):
    _redirect_paths(monkeypatch, tmp_path)
    (tmp_path / "state.json").write_text("not json {")
    assert userconfig.load_state() == {}
