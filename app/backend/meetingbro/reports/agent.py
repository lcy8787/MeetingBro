"""Visual Design Agent using the existing JSON chat API and a bounded local tool loop.

Tool requests are data, never native code or arbitrary provider function calls.
Only a model's explicit request spends an additional completion; invalid final
reports do not cause hidden paid retries.
"""
from __future__ import annotations

import json
from copy import deepcopy

from ..schemas import ReportDocument
from .components import TOOL_NAMES, generation_schema, list_components, planning_prompt
from .design import compose_layout, review_design, validate_layout


class VisualDesignAgent:
    MAX_TURNS = 5

    def __init__(self, client, validate, *, max_turns: int | None = None):
        self.client = client
        self.validate = validate
        self.max_turns = self.MAX_TURNS if max_turns is None else min(self.MAX_TURNS, max_turns)
        if self.max_turns < 1:
            raise ValueError('Visual Design Agent needs at least one model turn')

    def run(self, evidence: dict, chunk, language: str) -> ReportDocument:
        speaker_ids = sorted({item['speaker_id'] for item in evidence['transcript'] if item.get('speaker_id')})
        schema = generation_schema(speaker_ids)
        evidence = {**evidence, 'allowed_speaker_ids': speaker_ids}
        if self.max_turns == 1:
            schema['properties']['tool_request'] = {'type': 'null'}
        system = planning_prompt(language, schema=schema)
        if self.max_turns == 1:
            system += '\nThis run has one completion for this transcript part. Publish the complete report now; tool_request must be null. The registry and DSL are already available above.'
        context = dict(evidence)
        candidate = None
        for turn in range(self.max_turns):
            raw = self.client.chat(system=system, user=json.dumps(context, ensure_ascii=False),
                                   max_tokens=7200, temperature=0.1, response_schema=schema)
            # Keep legacy fenced final responses readable, using the established validator.
            from .generator import ReportGenerationError, _payload
            payload = _payload(raw)
            request = payload.pop('tool_request', None)
            if request is None:
                return self.validate(json.dumps(payload, ensure_ascii=False), chunk)
            if turn == self.max_turns - 1:
                raise ValueError('Visual Design Agent exhausted its tool budget without publishing')
            if not isinstance(request, dict) or set(request) - {'name', 'query', 'section_index'}:
                raise ValueError('Invalid design tool request')
            name = request.get('name')
            if name not in TOOL_NAMES:
                raise ValueError('Unknown design tool')
            result = None
            try:
                if name == 'list_components':
                    query = request.get('query', '')
                    if not isinstance(query, str) or len(query) > 200:
                        raise ValueError('Invalid registry query')
                    result = list_components(query)
                else:
                    if name == 'revise_section':
                        index = request.get('section_index')
                        if candidate is None or type(index) is not int or not 0 <= index < len(candidate.sections):
                            raise ValueError('Revision requires an existing candidate and valid section index')
                        if len(payload.get('sections', [])) != 1:
                            raise ValueError('Revision requires exactly one replacement section')
                        updated = candidate.model_dump(exclude={'design_review', 'warnings', 'generation_method', 'takeaways'})
                        updated['sections'][index] = payload['sections'][0]
                        proposed = self.validate(json.dumps(updated, ensure_ascii=False), chunk)
                    elif payload.get('sections'):
                        proposed = self.validate(json.dumps(payload, ensure_ascii=False), chunk)
                    elif candidate is not None:
                        proposed = deepcopy(candidate)
                    else:
                        raise ValueError('Tool needs a candidate report')
                    if name == 'compose_layout':
                        for section in proposed.sections:
                            if section.layout is None:
                                section.layout = compose_layout(section)
                            validate_layout(section)
                    findings = review_design(proposed, repair=name == 'review_design')
                    candidate = proposed  # Commit only a fully validated tool result.
                    result = {'valid': True, 'candidate': candidate.model_dump(),
                              'findings': [finding.model_dump() for finding in findings]}
            except (ValueError, TypeError, KeyError, ReportGenerationError) as exc:
                result = {'valid': False, 'error': str(exc)}
            # Bound history: original evidence + latest tool result only.
            context = {**evidence, 'tool_result': {'name': name, 'result': result},
                       'turns_remaining': self.max_turns - turn - 1}
        raise ValueError('No visual report published')
