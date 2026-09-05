"""Fixed test-only observations of real Hermes APIs, also runnable without pytest.

Inputs are data for named APIs. Nothing here evaluates case-provided Python,
interprets instructions, derives expected results, or launches a shell.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
from importlib.resources import files
import json
from pathlib import Path
import sys
import tempfile
import unicodedata

from plugins.workflow import bash_rendering, conditions, language_schema
from plugins.workflow.models import WorkflowValidationError

TOKEN_APIS = frozenset({
    'iter_output_references', 'iter_loop_previous_output_references',
    'iter_output_references_in_spans', 'iter_when_output_references',
    'bash_output_references', 'bash_loop_previous_output_references',
})
SPAN_APIS = frozenset({
    'iter_output_reference_candidate_spans', 'classify_bash_reference_spans',
    'bash_loop_previous_reference_spans',
})
CONDITION_APIS = frozenset({'validate_v3_condition_syntax', 'validate_v6_condition_syntax'})
SCANNER_RESULT_KEYS = {
    **{name: 'tokens' for name in TOKEN_APIS},
    **{name: 'spans' for name in SPAN_APIS},
    **{name: 'references' for name in CONDITION_APIS},
    'contains_output_reference': 'value',
    'validate_authenticated_resource_references': 'value',
    'compute_package_digest': 'value',
}
SUBSTITUTION_APIS = frozenset({
    'StrictSubstitutionRenderer.render_prompt',
    'StrictSubstitutionRenderer.render_outputs',
    'StrictSubstitutionRenderer.render_bash',
})
PATH_APIS = frozenset({'_v3_output_path_impossible', 'resolve_output_reference'})
PROFILES = {
    (3, 11): ('python-3.11-unicode-14.0.0', '14.0.0'),
    (3, 12): ('python-3.12-unicode-15.0.0', '15.0.0'),
    (3, 13): ('python-3.13-unicode-15.1.0', '15.1.0'),
}


def profile_id() -> str:
    profile, database = PROFILES[sys.version_info[:2]]
    assert unicodedata.unidata_version == database, (
        f'{profile} requires Unicode {database}, got {unicodedata.unidata_version}'
    )
    return profile


def _token(token) -> dict[str, object]:
    return {'node_id': token.node_id, 'path': list(token.path),
            'start': token.start, 'end': token.end}


def _error(exc: Exception) -> dict[str, object]:
    result: dict[str, object] = {'class': type(exc).__name__}
    for name in ('code', 'start', 'node_id', 'path'):
        value = getattr(exc, name, None)
        if value is not None:
            result[name] = list(value) if isinstance(value, tuple) else value
    if isinstance(exc, WorkflowValidationError):
        result['issues'] = [{'code': issue.code, 'path': issue.path} for issue in exc.issues]
    if exc.__cause__ is not None:
        cause = {'class': type(exc.__cause__).__name__}
        if getattr(exc.__cause__, 'code', None) is not None:
            cause['code'] = exc.__cause__.code
        result['cause'] = cause
    return result


def _consume(iterator, consume, serialize, values):
    # Consume *inside* the caller's exception boundary. Never list(iterator) first:
    # that would lose the successful prefix when a later next() raises.
    if consume == 'first':
        item = next(iterator, None)
        if item is not None:
            values.append(serialize(item))
    elif consume == 'all':
        for item in iterator:
            values.append(serialize(item))
    else:
        raise ValueError('unknown iterator consumption policy')


def _resource_package(case, tmp_path):
    from plugins.workflow.schema import (
        _compile_workflow_source_document, load_workflow_snapshot, parse_workflow_source_bytes,
    )
    data = case['input']
    definition = data['definition_yaml'].encode('utf-8')
    companion = data['companion_yaml'].encode('utf-8')
    path = tmp_path / 'example.yaml'
    if case['api'] == 'compute_package_digest':
        tmp_path.mkdir(parents=True, exist_ok=True)
        path.write_bytes(definition)
        path.with_name('example.hermes.yaml').write_bytes(companion)
        for relative, hex_bytes in data['resource_hex'].items():
            resource = tmp_path / relative
            # This is containment of the fixed temporary package fixture, not
            # reference prevalidation. Resource bytes go to trust.py unchanged.
            if Path(relative).is_absolute() or '..' in Path(relative).parts:
                raise ValueError('resource fixture must be relative and contained')
            resource.parent.mkdir(parents=True, exist_ok=True)
            resource.write_bytes(bytes.fromhex(hex_bytes))
        return load_workflow_snapshot(path, workflow_bytes=definition,
                                      sidecar_bytes=companion,
                                      normalizer_version=case['normalizer_version'])
    source = parse_workflow_source_bytes(path, workflow_bytes=definition,
                                        sidecar_bytes=companion)
    return _compile_workflow_source_document(
        source, normalizer_version=case['normalizer_version'])


def observe_scanner_case(case: dict[str, object], tmp_path: Path) -> dict[str, object]:
    api = case['api']
    if api not in SCANNER_RESULT_KEYS:
        raise ValueError(f'unknown scanner API: {api}')
    data = case['input']
    version = case['normalizer_version']
    key = SCANNER_RESULT_KEYS[api]
    result = {key: [] if key in {'tokens', 'spans', 'references'} else None, 'error': None}
    try:
        if api == 'iter_output_references':
            iterator = language_schema.iter_output_references(data['text'], normalizer_version=version)
        elif api == 'iter_loop_previous_output_references':
            iterator = language_schema.iter_loop_previous_output_references(data['text'], normalizer_version=version)
        elif api == 'iter_output_references_in_spans':
            iterator = language_schema.iter_output_references_in_spans(data['text'], data['spans'], normalizer_version=version)
        elif api == 'iter_when_output_references':
            iterator = language_schema.iter_when_output_references(data['text'], normalizer_version=version)
        elif api == 'iter_output_reference_candidate_spans':
            iterator = language_schema.iter_output_reference_candidate_spans(data['text'], normalizer_version=version)
        elif api == 'bash_output_references':
            result[key] = [_token(t) for t in bash_rendering.bash_output_references(data['text'], normalizer_version=version)]
        elif api == 'bash_loop_previous_output_references':
            result[key] = [_token(t) for t in bash_rendering.bash_loop_previous_output_references(data['text'])]
        elif api == 'bash_loop_previous_reference_spans':
            result[key] = [list(s) for s in bash_rendering.bash_loop_previous_reference_spans(data['text'])]
        elif api == 'classify_bash_reference_spans':
            result[key] = [list(s) for s in bash_rendering.classify_bash_reference_spans(data['text'], data['spans'])]
        elif api == 'contains_output_reference':
            result[key] = language_schema.contains_output_reference(data['text'], normalizer_version=version)
        elif api in CONDITION_APIS:
            references = (conditions.validate_v3_condition_syntax(data['text'])
                          if api == 'validate_v3_condition_syntax'
                          else conditions.validate_v6_condition_syntax(data['text']))
            result[key] = [dict(_token(t), kind='previous' if isinstance(t, conditions._PreviousOutputReference) else 'current') for t in references]
        elif api == 'validate_authenticated_resource_references':
            from plugins.workflow.schema import validate_authenticated_resource_references
            bodies = validate_authenticated_resource_references(
                _resource_package(case, tmp_path), command_bodies=data['command_bodies'],
                named_script_bodies=data['named_script_bodies'])
            result[key] = {'command_bodies': dict(bodies.command_bodies),
                           'named_script_bodies': dict(bodies.named_script_bodies)}
        elif api == 'compute_package_digest':
            from plugins.workflow.trust import compute_package_digest
            digest = compute_package_digest(_resource_package(case, tmp_path))
            # Stable applicable outcome, without freezing irrelevant YAML hash bytes.
            result[key] = {'sha256_valid': len(digest.sha256) == 64 and all(c in '0123456789abcdef' for c in digest.sha256),
                           'covered_relative_paths': list(digest.covered_relative_paths)}
        if api.startswith('iter_'):
            _consume(iterator, data['consume'], list if key == 'spans' else _token, result[key])
    except Exception as exc:
        result['error'] = _error(exc)
    return result


def _facet(data, node_id):
    # Same immutable facet constructor as test_strict_output_references._resolved_output.
    from plugins.workflow.output_resolution import ResolvedNodeOutput
    if data is None:
        return None
    value, structured = data['value'], data.get('structured', True)
    canonical = (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
                 if structured else value).encode('utf-8')
    return ResolvedNodeOutput(canonical_bytes=canonical, value=value,
        text=canonical.decode('utf-8'),
        media_type='application/json' if structured else 'text/markdown; charset=utf-8',
        sha256=hashlib.sha256(canonical).hexdigest(), node_id=node_id,
        attempt_id='attempt-winner', publication_id='a' * 32,
        schema_fingerprint='3' * 64 if structured else None, canonicalization_version=1)


def observe_substitution_case(case: dict[str, object], tmp_path: Path) -> dict[str, object]:
    from plugins.workflow.resources import VariableContext, StrictSubstitutionRenderer
    api = case['api']
    if api not in SUBSTITUTION_APIS:
        raise ValueError(f'unknown substitution API: {api}')
    data = case['input']
    result = {'rendered_text': None, 'error': None}
    try:
        values = dict(data.get('scalars', {}))
        for name in ('artifacts_dir', 'docs_dir'):
            if name in values:
                values[name] = Path(values[name])
        for name in ('node_outputs', 'current_body_outputs', 'allowed_outer_outputs', 'previous_body_outputs'):
            values[name] = {node: _facet(value, node) for node, value in data.get(name, {}).items()}
        renderer = StrictSubstitutionRenderer(
            VariableContext(**values, normalizer_version=case['normalizer_version']),
            frozenset(data['direct_dependencies']))
        if api == 'StrictSubstitutionRenderer.render_prompt':
            result['rendered_text'] = renderer.render_prompt(data['text'])
        elif api == 'StrictSubstitutionRenderer.render_outputs':
            result['rendered_text'] = renderer.render_outputs(data['text'])
        else:
            rendered = renderer.render_bash(data['text'], spill_directory=tmp_path / 'spill', secure_v3=True)
            try:
                result['rendered_text'] = rendered.command
            finally:
                rendered.close()
    except Exception as exc:
        result['error'] = _error(exc)
    return result


def _json_value(value):
    if isinstance(value, Mapping):
        return {key: _json_value(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_json_value(child) for child in value]
    return value


def observe_structured_path_case(case: dict[str, object]) -> dict[str, object]:
    from plugins.workflow.schema import _v3_output_path_impossible
    from plugins.workflow.output_resolution import resolve_output_reference
    api = case['api']
    if api not in PATH_APIS:
        raise ValueError(f'unknown structured-path API: {api}')
    data = case['input']
    result = {'value': None, 'error': None}
    try:
        if api == '_v3_output_path_impossible':
            result['value'] = _v3_output_path_impossible(data['schema'], tuple(data['path']))
        else:
            output = resolve_output_reference(_facet(data['output'], data['node_id']),
                node_id=data['node_id'], path=tuple(data['path']))
            result['value'] = {'typed_value': _json_value(output.typed_value),
                               'rendered_text': output.rendered_text}
    except Exception as exc:
        result['error'] = _error(exc)
    return result


def main():
    profile = profile_id()
    corpus = json.loads(files('plugins.workflow').joinpath('conformance/reference_scanner_v1.json').read_text(encoding='utf-8'))
    count = 0
    with tempfile.TemporaryDirectory(prefix='reference-observations-') as root:
        for section, cases in corpus.items():
            for case in cases:
                if case['unicode_profiles'] != ['all'] and profile not in case['unicode_profiles']:
                    continue
                path = Path(root) / case['id']
                if section == 'scanner_cases':
                    actual = observe_scanner_case(case, path)
                elif section == 'substitution_cases':
                    actual = observe_substitution_case(case, path)
                elif section == 'structured_path_cases':
                    actual = observe_structured_path_case(case)
                else:
                    raise ValueError(f'unknown case section: {section}')
                if actual != case['expected']:
                    raise AssertionError(f"{profile} {case['id']}: {actual!r} != {case['expected']!r}")
                count += 1
    print(json.dumps({'profile': profile, 'passed': count}))


if __name__ == '__main__':
    main()
