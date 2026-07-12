from hermes_cli.brand_config import seed_disabled


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
