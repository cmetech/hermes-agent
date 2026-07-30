"""Tests for the browser profile registry (config parsing + defaults)."""

import pytest

from tools import browser_profiles


class TestLoadProfiles:
    def test_absent_config_yields_only_builtin_default(self, monkeypatch):
        """No browser.profiles block → exactly one ephemeral 'default' profile."""
        monkeypatch.setattr(browser_profiles, "_read_config", lambda: {})
        profiles = browser_profiles.load_profiles()
        assert list(profiles) == ["default"]
        assert profiles["default"].kind == "ephemeral"
        assert profiles["default"].trusted_origins == ()

    def test_enrolled_profile_is_parsed(self, monkeypatch):
        monkeypatch.setattr(
            browser_profiles,
            "_read_config",
            lambda: {
                "browser": {
                    "profiles": {
                        "enrolled": {
                            "kind": "enrolled",
                            "executable": "auto",
                            "user_data_dir": "/tmp/enrolled",
                            "cdp_port": 9222,
                            "headed": True,
                            "trusted_origins": ["https://wiki.corp.example"],
                        }
                    }
                }
            },
        )
        prof = browser_profiles.get_profile("enrolled")
        assert prof is not None
        assert prof.kind == "enrolled"
        assert prof.cdp_port == 9222
        assert prof.headed is True
        assert prof.trusted_origins == ("https://wiki.corp.example",)

    def test_unknown_profile_returns_none(self, monkeypatch):
        monkeypatch.setattr(browser_profiles, "_read_config", lambda: {})
        assert browser_profiles.get_profile("nope") is None

    def test_malformed_profiles_block_falls_back_to_default(self, monkeypatch):
        """A non-dict profiles value must not raise — fail open to default."""
        monkeypatch.setattr(
            browser_profiles, "_read_config", lambda: {"browser": {"profiles": "garbage"}}
        )
        profiles = browser_profiles.load_profiles()
        assert list(profiles) == ["default"]

    def test_unknown_kind_is_rejected(self, monkeypatch):
        """An unrecognized kind is dropped rather than trusted."""
        monkeypatch.setattr(
            browser_profiles,
            "_read_config",
            lambda: {"browser": {"profiles": {"weird": {"kind": "wat"}}}},
        )
        assert browser_profiles.get_profile("weird") is None

    def test_builtin_default_cannot_be_given_trusted_origins(self, monkeypatch):
        """Hard rule: the ephemeral default profile is never origin-trusted."""
        monkeypatch.setattr(
            browser_profiles,
            "_read_config",
            lambda: {
                "browser": {
                    "profiles": {
                        "default": {"kind": "ephemeral",
                                    "trusted_origins": ["https://evil.example"]}
                    }
                }
            },
        )
        assert browser_profiles.get_profile("default").trusted_origins == ()


class TestPortCollision:
    def test_duplicate_enrolled_ports_are_rejected(self, monkeypatch, caplog):
        """Two profiles on one port bind one profile's trust to another's browser."""
        monkeypatch.setattr(
            browser_profiles, "_read_config",
            lambda: {"browser": {"profiles": {
                "a": {"kind": "enrolled", "cdp_port": 9222},
                "b": {"kind": "enrolled", "cdp_port": 9222},
            }}},
        )
        with caplog.at_level("WARNING"):
            profiles = browser_profiles.load_profiles()
        assert "a" in profiles
        assert "b" not in profiles
        assert any("cdp_port" in r.message for r in caplog.records)

    def test_distinct_ports_both_load(self, monkeypatch):
        monkeypatch.setattr(
            browser_profiles, "_read_config",
            lambda: {"browser": {"profiles": {
                "a": {"kind": "enrolled", "cdp_port": 9222},
                "b": {"kind": "enrolled", "cdp_port": 9333},
            }}},
        )
        profiles = browser_profiles.load_profiles()
        assert {"a", "b"} <= set(profiles)


class TestMisconfiguredTrustedOrigins:
    """A string instead of a list must warn, not silently trust nothing.

    `hermes config set` coerces only bool/int/float, so
    `config set ...trusted_origins "https://a,https://b"` stores a STRING. The
    parser correctly refuses to trust it, but silently — the user would see no
    access and no explanation.
    """

    def _cfg(self, origins):
        return {
            "browser": {
                "profiles": {
                    "enrolled": {"kind": "enrolled", "trusted_origins": origins}
                }
            }
        }

    def test_string_origins_grant_no_trust(self, monkeypatch):
        monkeypatch.setattr(
            browser_profiles, "_read_config",
            lambda: self._cfg("https://wiki.corp.example,https://jira.corp.example"),
        )
        assert browser_profiles.get_profile("enrolled").trusted_origins == ()

    def test_string_origins_log_a_warning(self, monkeypatch, caplog):
        monkeypatch.setattr(
            browser_profiles, "_read_config", lambda: self._cfg("https://wiki.corp.example")
        )
        with caplog.at_level("WARNING"):
            browser_profiles.get_profile("enrolled")
        assert any(
            "trusted_origins" in r.message and "list" in r.message for r in caplog.records
        ), f"expected a trusted_origins list warning, got: {[r.message for r in caplog.records]}"

    def test_proper_list_does_not_warn(self, monkeypatch, caplog):
        monkeypatch.setattr(
            browser_profiles, "_read_config", lambda: self._cfg(["https://wiki.corp.example"])
        )
        with caplog.at_level("WARNING"):
            prof = browser_profiles.get_profile("enrolled")
        assert prof.trusted_origins == ("https://wiki.corp.example",)
        assert not [r for r in caplog.records if "trusted_origins" in r.message]

    def test_absent_origins_do_not_warn(self, monkeypatch, caplog):
        monkeypatch.setattr(
            browser_profiles, "_read_config",
            lambda: {"browser": {"profiles": {"enrolled": {"kind": "enrolled"}}}},
        )
        with caplog.at_level("WARNING"):
            browser_profiles.get_profile("enrolled")
        assert not [r for r in caplog.records if "trusted_origins" in r.message]


def _enrolled(*origins):
    return browser_profiles.BrowserProfile(
        name="enrolled",
        kind=browser_profiles.KIND_ENROLLED,
        trusted_origins=tuple(origins),
    )


class TestIsOriginTrusted:
    def test_exact_origin_match(self):
        p = _enrolled("https://wiki.corp.example")
        assert browser_profiles.is_origin_trusted(p, "https://wiki.corp.example/page/123")

    def test_scheme_must_match(self):
        """http:// must not inherit trust granted to https://."""
        p = _enrolled("https://wiki.corp.example")
        assert not browser_profiles.is_origin_trusted(p, "http://wiki.corp.example/x")

    def test_wildcard_matches_subdomain(self):
        p = _enrolled("https://*.corp.example")
        assert browser_profiles.is_origin_trusted(p, "https://wiki.corp.example/x")

    def test_wildcard_does_not_match_apex(self):
        """'*.corp.example' grants subdomains only, not corp.example itself."""
        p = _enrolled("https://*.corp.example")
        assert not browser_profiles.is_origin_trusted(p, "https://corp.example/x")

    def test_wildcard_does_not_match_suffix_lookalike(self):
        """The classic bypass: evilcorp.example must not match *.corp.example."""
        p = _enrolled("https://*.corp.example")
        assert not browser_profiles.is_origin_trusted(p, "https://evilcorp.example/x")

    def test_wildcard_is_not_a_bare_substring(self):
        p = _enrolled("https://*.corp.example")
        assert not browser_profiles.is_origin_trusted(p, "https://corp.example.evil.test/x")

    def test_port_mismatch_is_untrusted(self):
        p = _enrolled("https://wiki.corp.example")
        assert not browser_profiles.is_origin_trusted(p, "https://wiki.corp.example:8443/x")

    def test_ephemeral_profile_is_never_trusted(self):
        p = browser_profiles.BrowserProfile(name="default", kind=browser_profiles.KIND_EPHEMERAL)
        assert not browser_profiles.is_origin_trusted(p, "https://wiki.corp.example/x")

    def test_no_origins_is_never_trusted(self):
        assert not browser_profiles.is_origin_trusted(_enrolled(), "https://wiki.corp.example/x")

    def test_garbage_url_denies(self):
        p = _enrolled("https://wiki.corp.example")
        assert not browser_profiles.is_origin_trusted(p, "not a url")
        assert not browser_profiles.is_origin_trusted(p, "")

    def test_none_profile_denies(self):
        assert not browser_profiles.is_origin_trusted(None, "https://wiki.corp.example/x")

    def test_userinfo_spoof_denies(self):
        """https://wiki.corp.example@evil.test must resolve to evil.test → deny."""
        p = _enrolled("https://wiki.corp.example")
        assert not browser_profiles.is_origin_trusted(
            p, "https://wiki.corp.example@evil.test/x"
        )


class TestResolveExecutable:
    def test_explicit_path_is_used_when_it_exists(self, monkeypatch, tmp_path):
        exe = tmp_path / "msedge"
        exe.write_text("")
        exe.chmod(0o755)
        p = browser_profiles.BrowserProfile(
            name="enrolled", kind=browser_profiles.KIND_ENROLLED, executable=str(exe)
        )
        assert browser_profiles.resolve_executable(p) == str(exe)

    def test_explicit_missing_path_returns_none(self):
        p = browser_profiles.BrowserProfile(
            name="enrolled",
            kind=browser_profiles.KIND_ENROLLED,
            executable="/nonexistent/msedge",
        )
        assert browser_profiles.resolve_executable(p) is None

    def test_auto_probes_platform_candidates(self, monkeypatch, tmp_path):
        found = tmp_path / "Microsoft Edge"
        found.write_text("")
        found.chmod(0o755)
        monkeypatch.setattr(
            browser_profiles, "_enrolled_candidates", lambda: [str(tmp_path / "nope"), str(found)]
        )
        p = browser_profiles.BrowserProfile(
            name="enrolled", kind=browser_profiles.KIND_ENROLLED, executable="auto"
        )
        assert browser_profiles.resolve_executable(p) == str(found)

    def test_auto_with_no_candidate_returns_none(self, monkeypatch):
        monkeypatch.setattr(browser_profiles, "_enrolled_candidates", lambda: [])
        p = browser_profiles.BrowserProfile(
            name="enrolled", kind=browser_profiles.KIND_ENROLLED, executable="auto"
        )
        assert browser_profiles.resolve_executable(p) is None

    def test_ephemeral_profile_resolves_to_none(self):
        """Ephemeral profiles use agent-browser's bundled Chrome, not an OS browser."""
        p = browser_profiles.BrowserProfile(name="default", kind=browser_profiles.KIND_EPHEMERAL)
        assert browser_profiles.resolve_executable(p) is None

    def test_candidates_are_platform_specific(self, monkeypatch):
        monkeypatch.setattr(browser_profiles.sys, "platform", "win32")
        assert any("Edge" in c for c in browser_profiles._enrolled_candidates())
        monkeypatch.setattr(browser_profiles.sys, "platform", "darwin")
        assert any("Microsoft Edge" in c for c in browser_profiles._enrolled_candidates())


class TestCandidateOrder:
    """Chrome first, Edge fallback — confirmed against the target fleet 2026-07-26.

    Chrome reaches internal sites there, so it is policy-managed. The bundled
    Chrome for Testing stays excluded on every platform: it is a different
    binary, and the one browser that cannot present machine certificates.
    """

    PLATFORMS = ("win32", "darwin", "linux")

    def _candidates(self, monkeypatch, platform):
        monkeypatch.setattr(browser_profiles.sys, "platform", platform)
        return browser_profiles._enrolled_candidates()

    @pytest.mark.parametrize("platform", PLATFORMS)
    def test_chrome_is_preferred_over_edge(self, monkeypatch, platform):
        candidates = self._candidates(monkeypatch, platform)
        first_chrome = next(i for i, c in enumerate(candidates) if "hrome" in c)
        first_edge = next(i for i, c in enumerate(candidates) if "dge" in c)
        assert first_chrome < first_edge

    @pytest.mark.parametrize("platform", PLATFORMS)
    def test_both_browsers_are_offered(self, monkeypatch, platform):
        candidates = self._candidates(monkeypatch, platform)
        assert any("hrome" in c for c in candidates)
        assert any("dge" in c for c in candidates)

    def test_windows_lists_real_chrome_paths(self, monkeypatch):
        candidates = self._candidates(monkeypatch, "win32")
        assert any(c.lower().endswith("chrome.exe") for c in candidates)
        assert any(
            "Google" in c and "Chrome" in c and "Application" in c for c in candidates
        )

    @pytest.mark.parametrize("platform", PLATFORMS)
    def test_chrome_for_testing_is_never_a_candidate(self, monkeypatch, platform):
        """The bundled throwaway browser is precisely what this list excludes."""
        for candidate in self._candidates(monkeypatch, platform):
            lowered = candidate.lower().replace(" ", "-")
            assert "chrome-for-testing" not in lowered
            assert "chrome-headless-shell" not in lowered
            assert "agent-browser" not in lowered
            assert "ms-playwright" not in lowered


class TestResolveUserDataDir:
    """M-3: ``${HERMES_HOME}`` never reached ``expandvars`` with a value.

    HERMES_HOME is only exported to ``os.environ`` on the ``--profile`` override
    path. On the plain CLI and on ``hermes serve`` it is unset, so the old
    ``os.path.expandvars`` returned the LITERAL and ``os.makedirs`` created
    ``./${HERMES_HOME}/browser-profiles/enrolled`` relative to the CWD -- the
    persistent SSO profile the whole feature exists for did not survive a
    directory change.
    """

    @staticmethod
    def _profile(user_data_dir, name="enrolled"):
        return browser_profiles.BrowserProfile(
            name=name,
            kind=browser_profiles.KIND_ENROLLED,
            user_data_dir=user_data_dir,
        )

    @pytest.fixture()
    def _home(self, tmp_path, monkeypatch):
        import hermes_constants

        home = tmp_path / "brandhome"
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: home)
        return home

    def test_unset_hermes_home_still_yields_an_absolute_path(self, _home):
        resolved = browser_profiles.resolve_user_data_dir(
            self._profile("${HERMES_HOME}/browser-profiles/enrolled")
        )
        import os

        assert os.path.isabs(resolved)
        assert resolved == str(_home / "browser-profiles" / "enrolled")

    def test_the_literal_token_never_survives(self, _home):
        for raw in ("${HERMES_HOME}/p", "$HERMES_HOME/p", "%HERMES_HOME%/p"):
            resolved = browser_profiles.resolve_user_data_dir(self._profile(raw))
            assert "HERMES_HOME" not in resolved, raw
            assert resolved == str(_home / "p")

    def test_env_var_path_still_works(self, tmp_path, monkeypatch):
        """Where HERMES_HOME IS exported, the resolver agrees with it."""
        import hermes_constants

        home = tmp_path / "envhome"
        monkeypatch.setenv("HERMES_HOME", str(home))
        # get_hermes_home() reads HERMES_HOME; assert against the real resolver.
        assert str(hermes_constants.get_hermes_home()) == str(home)
        resolved = browser_profiles.resolve_user_data_dir(
            self._profile("${HERMES_HOME}/browser-profiles/enrolled")
        )
        assert resolved == str(home / "browser-profiles" / "enrolled")

    def test_a_relative_path_is_made_absolute(self, _home):
        import os

        resolved = browser_profiles.resolve_user_data_dir(self._profile("rel/dir"))
        assert os.path.isabs(resolved)

    def test_empty_user_data_dir_falls_back_under_the_home(self, _home):
        resolved = browser_profiles.resolve_user_data_dir(self._profile("", name="corp"))
        assert resolved == str(_home / "browser-profiles" / "corp")
