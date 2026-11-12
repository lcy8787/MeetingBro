"""Visual Design Agent using the existing JSON chat API and a bounded local tool loop.

Tool requests are data, never native code or arbitrary provider function calls.
Only a model's explicit request spends an additional completion; invalid final
reports do not cause hidden paid retries.
"""
from __future__ import annotations

import json
from copy import deepcopy

from ..schemas import ReportDocument
from .components import REPORT_SCHEMA, TOOL_NAMES, list_components, planning_prompt
from .design import compose_layout, review_design, validate_layout


class VisualDesignAgent:
    MAX_TURNS = 5

    def __init__(self, client, validate):
        self.client = client
        self.validate = validate

    def run(self, evidence: dict, chunk, language: str) -> ReportDocument:
        context = dict(evidence)
        candidate = None
        for turn in range(self.MAX_TURNS):
            raw = self.client.chat(system=planning_prompt(language), user=json.dumps(context, ensure_ascii=False),
                                   max_tokens=7200, temperature=0.1, response_schema=REPORT_SCHEMA)
            # Keep legacy fenced final responses readable, using the established validator.
            from .generator import _payload
            payload = _payload(raw)
            request = payload.pop('tool_request', None)
            if request is None:
                return self.validate(json.dumps(payload, ensure_ascii=False), chunk)
            if turn == self.MAX_TURNS - 1:
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
            except (ValueError, TypeError, KeyError) as exc:
                result = {'valid': False, 'error': str(exc)}
            # Bound history: original evidence + latest tool result only.
            context = {**evidence, 'tool_result': {'name': name, 'result': result},
                       'turns_remaining': self.MAX_TURNS - turn - 1}
        raise ValueError('No visual report published')
