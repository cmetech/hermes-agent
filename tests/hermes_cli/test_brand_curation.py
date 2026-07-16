import json

from hermes_cli.brand_config import (
    get_excluded_toolsets,
    get_hidden_skills,
    get_managed_skills,
    get_skill_rename_map,
    seed_disabled,
)


def _write_brand(tmp_path, slug, curation):
    (tmp_path / "brands").mkdir(exist_ok=True)
    (tmp_path / "brands" / f"{slug}.json").write_text(
        json.dumps({"slug": slug, "curation": curation}), encoding="utf-8"
    )
    return tmp_path


def test_get_hidden_skills_and_rename_and_toolsets(tmp_path):
    root = _write_brand(
        tmp_path,
        "acme",
        {
            "skills": {
                "exclude": ["p5js", "yuanbao"],
                "rename": {"hermes-agent": "Co-worker"},
            },
            "tools": {"excludeToolsets": ["homeassistant", "spotify"]},
        },
    )
    assert get_hidden_skills("acme", root) == {"p5js", "yuanbao"}
    assert get_skill_rename_map("acme", root) == {"hermes-agent": "Co-worker"}
    assert get_excluded_toolsets("acme", root) == {"homeassistant", "spotify"}


def test_get_managed_skills(tmp_path):
    root = _write_brand(
        tmp_path,
        "acme",
        {"skills": {"managed": ["gateway-toolcall-parity", "workflow-builder"]}},
    )
    assert get_managed_skills("acme", root) == {
        "gateway-toolcall-parity",
        "workflow-builder",
    }


def test_curation_helpers_fail_open(tmp_path):
    # Missing descriptor / missing curation sections → empty, never raise.
    assert get_hidden_skills("nope", tmp_path) == set()
    assert get_skill_rename_map("nope", tmp_path) == {}
    assert get_excluded_toolsets("nope", tmp_path) == set()
    assert get_managed_skills("nope", tmp_path) == set()
    root = _write_brand(tmp_path, "bare", {})
    assert get_hidden_skills("bare", root) == set()
    assert get_skill_rename_map("bare", root) == {}
    assert get_excluded_toolsets("bare", root) == set()
    assert get_managed_skills("bare", root) == set()


def test_seed_disabled_unions_skills_and_toolsets():
    config = {"skills": {"disabled": ["already-off"]}, "disabled_toolsets": ["x"]}
    brand = {
        "curation": {
            "skills": {"disabledByDefault": ["news"]},
            "tools": {"disabledByDefault": ["spotify"]},
        }
    }
    out = seed_disabled(config, brand)
    assert set(out["skills"]["disabled"]) == {"already-off", "news"}
    assert set(out["disabled_toolsets"]) == {"x", "spotify"}
    # original not mutated
    assert config["skills"]["disabled"] == ["already-off"]


def test_seed_disabled_tolerates_missing_sections():
    out = seed_disabled({}, {"curation": {"skills": {"disabledByDefault": ["a"]}}})
    assert out["skills"]["disabled"] == ["a"]
    assert out["disabled_toolsets"] == []
