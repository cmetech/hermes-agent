"""Reviewed literals exercise existing APIs; no expected value is scanner-generated."""
from importlib.resources import files
import json
from pathlib import Path

import pytest


from reference_scanner_observations import (
    PATH_APIS, SCANNER_RESULT_KEYS, SUBSTITUTION_APIS,
    observe_scanner_case, observe_structured_path_case, observe_substitution_case,
    profile_id,
)

RESOURCE = files('plugins.workflow').joinpath('conformance/reference_scanner_v1.json')
MANIFEST = Path(__file__).parent / 'fixtures/reference_scanner/case_manifest.json'
PROFILES = {
    'python-3.11-unicode-14.0.0',
    'python-3.12-unicode-15.0.0',
    'python-3.13-unicode-15.1.0',
}
FAMILIES = {
    'text-grammar', 'iterator-api', 'previous-outputs', 'bash-quotes',
    'bash-frames', 'bash-words', 'bash-input-operators', 'bash-boundaries',
    'scalars', 'schema-paths', 'unicode', 'authenticated-resources',
}


def test_literal_resource_matches_independent_manifest():
    assert RESOURCE.is_file(), 'Reviewed reference-scanner literal resource is absent'
    corpus = json.loads(RESOURCE.read_text(encoding='utf-8'))
    manifest = json.loads(MANIFEST.read_text())['cases']
    assert set(corpus) == {'scanner_cases', 'substitution_cases', 'structured_path_cases'}
    cases = [case for section in corpus.values() for case in section]
    assert len({case['id'] for case in cases}) == len(cases)
    assert len({row['id'] for row in manifest}) == len(manifest)
    assert {case['id'] for case in cases} == {row['id'] for row in manifest}
    rows = {row['id']: row for row in manifest}
    assert {family for row in manifest for family in row['families']} == FAMILIES
    for case in cases:
        assert case['requirements'] == sorted(case['requirements'])
        assert case['requirements'] == rows[case['id']]['requirements']
        assert rows[case['id']]['evidence']
        assert case['expected']
        assert case['unicode_profiles']
        assert set(case['unicode_profiles']) <= PROFILES | {'all'}
        assert 'normalizer_version' in case


def test_metadata_profile_ids_match_literal_profile_contract():
    from plugins.workflow.language_schema import workflow_authoring_contract
    from plugins.workflow.models import WorkflowLanguageProfile
    contract = workflow_authoring_contract(WorkflowLanguageProfile.ARCHON_2026_07, normalizer_version=6)
    metadata = contract['reference_scanner_v1']
    assert {p['id'] for p in metadata['unicode_profiles']['profiles']} == PROFILES



def _cases(section):
    if not RESOURCE.is_file():
        return []  # The explicit resource assertion gives the missing-artifact RED.
    profile = profile_id()
    return [pytest.param(case, id=case['id']) for case in json.loads(RESOURCE.read_text())[section]
            if case['unicode_profiles'] == ['all'] or profile in case['unicode_profiles']]


@pytest.mark.parametrize('case', _cases('scanner_cases'))
def test_scanner_literals(case, tmp_path):
    assert case['api'] in SCANNER_RESULT_KEYS
    assert set(case['expected']) == {SCANNER_RESULT_KEYS[case['api']], 'error'}
    assert observe_scanner_case(case, tmp_path) == case['expected']


@pytest.mark.parametrize('case', _cases('substitution_cases'))
def test_substitution_literals(case, tmp_path):
    assert case['api'] in SUBSTITUTION_APIS
    assert set(case['expected']) == {'rendered_text', 'error'}
    assert observe_substitution_case(case, tmp_path) == case['expected']


@pytest.mark.parametrize('case', _cases('structured_path_cases'))
def test_structured_path_literals(case):
    assert case['api'] in PATH_APIS
    assert set(case['expected']) == {'value', 'error'}
    assert observe_structured_path_case(case) == case['expected']


def test_observation_boundary_retains_lazy_prefix_and_eager_atomicity(tmp_path):
    token = {'node_id': 'a', 'path': [], 'start': 0, 'end': 9}
    case = {'api': 'iter_output_references', 'normalizer_version': 6,
            'input': {'text': '$a.output $b.outputx', 'consume': 'first'}}
    assert observe_scanner_case(case, tmp_path) == {'tokens': [token], 'error': None}
    case['input']['consume'] = 'all'
    assert observe_scanner_case(case, tmp_path) == {
        'tokens': [token], 'error': {'class': 'WorkflowReferenceSyntaxError',
                                   'code': 'output_reference_path_unsupported', 'start': 10}}
    case['api'] = 'bash_output_references'
    case['input']['text'] = 'echo $a.output $b.outputx'
    case['input']['consume'] = 'first'
    assert observe_scanner_case(case, tmp_path) == {
        'tokens': [], 'error': {'class': 'WorkflowReferenceSyntaxError',
                               'code': 'output_reference_path_unsupported', 'start': 15,
                               'cause': {'class': 'WorkflowReferenceSyntaxError',
                                         'code': 'output_reference_path_unsupported'}}}


def test_authenticated_observations_preserve_distinct_scoped_semantic_codes(tmp_path):
    case = {
        'api': 'validate_authenticated_resource_references', 'normalizer_version': 6,
        'input': {
            'definition_yaml': (
                'name: scoped-auth\ndescription: Scoped authenticated references\nnodes:\n'
                '  - id: g\n    loop_group:\n      until: done\n      max_iterations: 2\n'
                '      nodes:\n        - id: a\n          prompt: Produce\n'
                '        - id: b\n          command: consume\n'
            ),
            'companion_yaml': 'language_compatibility: archon-2026-07\n',
            'command_bodies': {}, 'named_script_bodies': {},
        },
    }
    observations = []
    for body, semantic in (
        ('$a.output', 'scoped-reference-missing-dependency'),
        ('$LOOP_PREV.missing.output', 'scoped-reference-unknown-producer'),
    ):
        case['input']['command_bodies'] = {'g/b': body}
        actual = observe_scanner_case(case, tmp_path)
        assert actual == {'value': None, 'error': {
            'class': 'WorkflowValidationError', 'issues': [{
                'code': 'loop_group_scope_invalid',
                'path': 'nodes[0].loop_group.nodes[1].command',
                'semantic_code': semantic,
            }],
        }}
        observations.append(actual)
    assert observations[0] != observations[1]


def test_literal_corpus_covers_scoped_auth_and_candidate_guarded_eof():
    cases = {case['id']: case for case in json.loads(RESOURCE.read_text())['scanner_cases']}
    assert {
        'resource.body-command-current-missing',
        'resource.body-command-previous-unknown',
        'bash.heredoc-eof-no-candidate', 'bash.heredoc-eof-live',
        'bash.quote-eof-no-candidate', 'bash.quote-eof-live',
        'bash.heredoc-missing',
    } <= set(cases)


def test_observation_boundary_predicate_and_quote_context(tmp_path):
    assert observe_scanner_case({
        'api': 'contains_output_reference', 'normalizer_version': 6,
        'input': {'text': '$bad.outputx $a.output'},
    }, tmp_path) == {'value': True, 'error': None}
    assert observe_scanner_case({
        'api': 'classify_bash_reference_spans', 'normalizer_version': 6,
        'input': {'text': 'echo "$a.output"', 'spans': [[6, 15]]},
    }, tmp_path) == {'spans': [[6, 15, '"']], 'error': None}


@pytest.mark.parametrize('observer', [observe_scanner_case, observe_substitution_case])
def test_unknown_api_is_a_harness_error(observer, tmp_path):
    with pytest.raises(ValueError, match='unknown'):
        observer({'api': '__import__'}, tmp_path)
    with pytest.raises(ValueError, match='unknown'):
        observe_structured_path_case({'api': '__import__'})


def test_direct_python_surrogates_are_characterized_outside_published_json(tmp_path):
    # Direct Python accepts surrogate strings; JSON interchange fixtures contain
    # Unicode scalar values only. Conditions have their own UTF-8 encoding guard.
    assert observe_scanner_case({
        'api': 'iter_output_references', 'normalizer_version': 6,
        'input': {'text': '\ud800 $a.output', 'consume': 'all'},
    }, tmp_path) == {'tokens': [{'node_id': 'a', 'path': [], 'start': 2, 'end': 11}], 'error': None}
    assert observe_scanner_case({
        'api': 'iter_output_references', 'normalizer_version': 6,
        'input': {'text': '$a.output\udfff', 'consume': 'all'},
    }, tmp_path) == {'tokens': [], 'error': {'class': 'WorkflowReferenceSyntaxError',
                                           'code': 'output_reference_path_unsupported', 'start': 0}}
    assert observe_scanner_case({
        'api': 'validate_v3_condition_syntax', 'normalizer_version': 6,
        'input': {'text': "$a.output == '\ud800'"},
    }, tmp_path) == {'references': [], 'error': {'class': 'WorkflowConditionError',
        'code': 'condition_runtime_syntax_invalid', 'cause': {'class': 'UnicodeEncodeError'}}}
    raw = RESOURCE.read_text(encoding='utf-8')
    def assert_scalars(value):
        if isinstance(value, str):
            assert not any(0xD800 <= ord(char) <= 0xDFFF for char in value)
        elif isinstance(value, dict):
            for key, child in value.items():
                assert_scalars(key)
                assert_scalars(child)
        elif isinstance(value, list):
            for child in value:
                assert_scalars(child)
    assert_scalars(json.loads(raw))


def test_supported_python_unicode_observation_matrix(tmp_path):
    import os
    import shutil
    import subprocess
    uv = shutil.which('uv')
    assert uv, 'Unicode matrix prerequisite missing: install uv and supported Python interpreters'
    root = Path(__file__).resolve().parents[3]
    helper = Path(__file__).with_name('reference_scanner_observations.py').resolve()
    before = RESOURCE.read_bytes()
    outcomes = []
    for version, database in [('3.11', '14.0.0'), ('3.12', '15.0.0'), ('3.13', '15.1.0')]:
        found = subprocess.run([uv, 'python', 'find', '--offline', version], capture_output=True, text=True)
        assert found.returncode == 0, f'Unicode matrix prerequisite missing: Python {version}: {found.stderr}'
        venv = tmp_path / ('python-' + version)
        created = subprocess.run([uv, 'venv', '--offline', '--python', found.stdout.strip(), str(venv)], capture_output=True, text=True)
        assert created.returncode == 0, f'Cannot provision Unicode matrix Python {version}: {created.stderr}'
        python = venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        installed = subprocess.run([uv, 'pip', 'install', '--offline', '--python', str(python), 'pyyaml', 'jsonschema', 'psutil'], capture_output=True, text=True)
        assert installed.returncode == 0, f'Unicode matrix prerequisite missing: cached pyyaml/jsonschema/psutil for {version}: {installed.stderr}'
        observed = subprocess.run([str(python), str(helper)], cwd=root,
            env={**os.environ, 'PYTHONPATH': str(root)}, capture_output=True, text=True)
        assert observed.returncode == 0, f'Unicode matrix observation {version} failed:\n{observed.stdout}\n{observed.stderr}'
        outcome = json.loads(observed.stdout)
        assert outcome['profile'] == f'python-{version}-unicode-{database}'
        expected_count = sum(
            case['unicode_profiles'] == ['all'] or outcome['profile'] in case['unicode_profiles']
            for cases in json.loads(before).values() for case in cases
        )
        assert outcome['passed'] == expected_count
        outcomes.append(outcome)
    assert {outcome['profile'] for outcome in outcomes} == PROFILES
    assert RESOURCE.read_bytes() == before, 'Observing supported profiles must never rewrite publication'
