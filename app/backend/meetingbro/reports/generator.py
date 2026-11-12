"""Generate reports in bounded transcript chunks with explicit provenance.

Citations and numeric checks reject some unsupported claims; they cannot prove
semantic correctness. A failed generation never becomes a transcript report.
"""
from __future__ import annotations

import json
import logging
from pydantic import ValidationError
import re
from typing import Protocol, Sequence

from ..llm.openai_compatible import LLMResponseError, extract_json_object
from ..schemas import DesignFinding, LanguageCode, ReportBlock, ReportDocument, ReportOption, ReportSection, TranscriptSegment
from ..report_contracts import CHART_TYPES, TABLE_TYPES, component_kind
from .design import validate_layout, review_design
from .evidence import numeric, validate_component_fields, validate_visual
from .agent import VisualDesignAgent
from .errors import ReportValidationError, structure_location, validation_at


_LOGGER = logging.getLogger(__name__)


class ChatClient(Protocol):
    def chat(self, *, system: str, user: str, max_tokens: int, temperature: float = 0.2,
             response_schema: dict | None = None) -> str: ...


class ReportGenerationError(RuntimeError):
    """A model did not produce a complete, validated report."""

    def __init__(self, message: str, *, code: str = 'invalid_report', location: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.location = location


from .quantities import ARABIC_NUMBERS as _NUMBERS, CHINESE_NUMBERS as _CHINESE_NUMBERS, number_tokens
_LANGUAGES = {"en": "English", "zh": "Traditional Chinese", "de": "German"}


def _chunks(segments: Sequence[TranscriptSegment], limit: int = 8000) -> list[list[tuple[str, str]]]:
    chunks: list[list[tuple[str, str]]] = []
    group: list[tuple[str, str]] = []
    size = 0
    for segment in segments:
        text = segment.text.strip()
        # Even an unusually long individual segment must not exceed the budget.
        for pos in range(0, len(text), limit):
            piece = text[pos:pos + limit]
            if group and size + len(piece) > limit:
                chunks.append(group)
                group, size = [], 0
            group.append((segment.id, piece))
            size += len(piece)
    if group:
        chunks.append(group)
    return chunks


def _payload(raw: str) -> dict:
    payload = extract_json_object(raw)
    # Harmless presentation fields some models add even though the provider
    # schema omits them; they are not content and must not invalidate a report.
    for key in ('takeaways', 'warnings', 'design_review', 'generation_method'):
        payload.pop(key, None)
    return payload


def _validate_text(text: str, ids: list[str], sources: dict[str, str]) -> None:
    if not ids or any(key not in sources for key in ids):
        raise ReportValidationError('Missing or unknown transcript sources', code='invalid_report_sources')
    evidence = ' '.join(sources[key] for key in ids)
    arabic, chinese, spoken = number_tokens(text)
    evidence_tokens = set().union(*number_tokens(evidence, source=True))
    if ('number', 1) in evidence_tokens:
        evidence_tokens.add(('indefinite', 1))
    if not arabic <= evidence_tokens:
        raise ReportValidationError('Unsupported number', code='unsupported_report_claim')
    if not chinese <= evidence_tokens:
        raise ReportValidationError('Unsupported Chinese number', code='unsupported_report_claim')
    if not spoken <= evidence_tokens:
        raise ReportValidationError('Unsupported spoken number', code='unsupported_report_claim')


def _supported_prose_sentences(value: str, ids: list[str], sources: dict[str, str]) -> str:
    """Omit whole unsupported sentences; never guess a replacement number or citation."""
    pieces = re.split(r'(?<=[。！？；、])|(?<=，)|(?<=[.!?])(?=\s+[A-Z])|(?<=[,;])(?=\s)|(?<=\n)', value)
    if len(pieces) < 2:
        return value
    kept = []
    for piece in pieces:
        if not piece.strip():
            kept.append(piece)
            continue
        try:
            _validate_text(piece, ids, sources)
            kept.append(piece)
        except ValueError as exc:
            if str(exc) not in {'Unsupported number', 'Unsupported Chinese number', 'Unsupported spoken number'}:
                raise
    result = ''.join(kept).strip()
    return re.sub(r'[，、；,;]\s*$', '', result).strip()


def _repair_chart_observations(block: ReportBlock, *, findings: list[DesignFinding], section_id: str) -> None:
    """Remove exact duplicate observations and degrade one-point charts losslessly.

    Duplicated chart rows are redundant, not new evidence. A single observation
    cannot be a comparison chart, so it is rendered as a sourced metric card
    rather than rejecting an otherwise valid report.
    """
    if block.type not in CHART_TYPES or not block.data:
        return
    normalized = []
    dropped = False
    for datum in block.data:
        raw = datum.value.strip()
        try:
            numeric(raw)
            candidate = raw
        except ValueError:
            tokens = re.findall(r'\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?', raw)
            candidate = tokens[0].replace(',', '') if len({token.replace(',', '') for token in tokens}) == 1 else None
            if candidate is None:
                dropped = True
                continue
            try:
                numeric(candidate)
            except ValueError:
                dropped = True
                continue
        if candidate != datum.value:
            datum.value = candidate
        normalized.append(datum)
    if dropped:
        findings.append(DesignFinding(section_id=section_id, code='chart_observation_removed',
            message='Removed a chart point without an exact numeric value; the surrounding prose keeps its content.', repaired=True))
    seen: dict[tuple[str, str], object] = {}
    unique = []
    removed = False
    for datum in normalized:
        key = (datum.series, datum.label)
        previous = seen.get(key)
        if previous is None:
            seen[key] = datum
            unique.append(datum)
            continue
        if (datum.value, datum.unit, datum.evidence_quote, datum.source_segment_ids) == (
                previous.value, previous.unit, previous.evidence_quote, previous.source_segment_ids):
            removed = True
            continue
        # Conflicting values for the same observation must still fail closed.
        unique.append(datum)
    if removed:
        findings.append(DesignFinding(section_id=section_id, code='duplicate_observation_removed',
            message='Removed duplicate identical chart observations without changing the remaining data.', repaired=True))
    block.data = unique
    if dropped and not block.data:
        block.type = 'EditorialText'
        findings.append(DesignFinding(section_id=section_id, code='chart_presentation_repaired',
            message='Displayed a chart without exact numeric observations as sourced prose.', repaired=True))
        return
    if 0 < len(block.data) < 2:
        block.options = [ReportOption(
            label=f'{datum.series} {datum.label}'.strip() or 'Value',
            text=f'{datum.value} {datum.unit}'.strip(),
            source_segment_ids=datum.source_segment_ids,
        ) for datum in block.data]
        block.data = []
        block.type = 'KeyMetrics'
        findings.append(DesignFinding(section_id=section_id, code='chart_presentation_repaired',
            message='Displayed a one-point chart as a sourced metric card because a comparison chart needs two observations.', repaired=True))


def _validated_block(item: dict, sources: dict[str, str], *, findings: list[DesignFinding], section_id: str, repair_prose: bool) -> ReportBlock:
    block = ReportBlock.model_validate(item)
    validate_component_fields(block)
    _repair_chart_observations(block, findings=findings, section_id=section_id)
    if not block.text.strip():
        raise ValueError('A report block has no text')
    visible = ' '.join((block.title, block.text, block.detail, *block.columns))
    try:
        _validate_text(visible, block.source_segment_ids, sources)
    except ValueError as exc:
        if (not repair_prose
                or str(exc) not in {'Unsupported number', 'Unsupported Chinese number', 'Unsupported spoken number'}):
            raise
        corrected_title = block.title
        try:
            _validate_text(corrected_title, block.source_segment_ids, sources)
        except ValueError as title_error:
            if str(title_error) not in {'Unsupported number', 'Unsupported Chinese number', 'Unsupported spoken number'}:
                raise
            corrected_title = ''  # Omit an unsupported heading rather than inventing a count.
        corrected_text = _supported_prose_sentences(block.text, block.source_segment_ids, sources)
        corrected_detail = _supported_prose_sentences(block.detail, block.source_segment_ids, sources)
        if not corrected_text:
            if not (block.options or block.data or block.nodes or block.edges
                    or block.cells or block.rows):
                raise
            # A sourced option list or visual encoding is the real content;
            # drop only the unsupported introductory claim.
            corrected_text = ''
        elif (corrected_title, corrected_text, corrected_detail) == (block.title, block.text, block.detail):
            raise
        # Headings and every remaining sentence must still pass the original source checks.
        _validate_text(' '.join((corrected_title, corrected_text, corrected_detail, *block.columns)), block.source_segment_ids, sources)
        heading_only = corrected_title != block.title and (corrected_text, corrected_detail) == (block.text, block.detail)
        block.title, block.text, block.detail = corrected_title, corrected_text, corrected_detail
        findings.append(DesignFinding(section_id=section_id,
            code='unsupported_heading_removed' if heading_only else 'unsupported_prose_removed',
            message='Omitted prose or a heading containing a quantity absent from its cited transcript evidence.', repaired=True))
    for option in block.options:
        if not option.label.strip() or not option.text.strip():
            raise ValueError('Interactive option has no label or explanation')
        try:
            _validate_text(option.label + ' ' + option.text, option.source_segment_ids, sources)
        except ValueError as exc:
            if not repair_prose or str(exc) not in {
                    'Unsupported number', 'Unsupported Chinese number', 'Unsupported spoken number'}:
                raise
            corrected = _supported_prose_sentences(option.text, option.source_segment_ids, sources)
            if not corrected or corrected == option.text:
                raise
            option.text = corrected
            _validate_text(option.label + ' ' + option.text, option.source_segment_ids, sources)
            findings.append(DesignFinding(section_id=section_id, code='unsupported_prose_removed',
                message='Omitted an option clause containing a quantity absent from its cited transcript evidence.',
                repaired=True))
    if block.type in TABLE_TYPES:
        if len(block.columns) < 2 or len(block.rows) < 2 or block.options:
            raise ValueError('Table needs comparable columns and rows')
        if any(not column.strip() for column in block.columns):
            raise ValueError('Empty table heading')
        for row in block.rows:
            if len(row.cells) != len(block.columns) or any(not cell.strip() for cell in row.cells):
                raise ValueError('Invalid table row')
            _validate_text(' '.join(row.cells), row.source_segment_ids, sources)
    elif block.columns or block.rows:
        raise ReportValidationError('Only tables may contain rows or columns', code='invalid_report_component_data', location='columns' if block.columns else 'rows')
    validate_visual(block, sources, _validate_text)
    # Only change presentation after every original field and citation is validated.
    if component_kind(block.type) == 'statistic':
        values = ' '.join((block.title, block.text, block.detail, *(o.label + ' ' + o.text for o in block.options)))
        if not any(number_tokens(values)):
            block.type = 'SummaryCard' if block.options else 'EditorialText'
            findings.append(DesignFinding(section_id=section_id, code='metric_presentation_repaired',
                message='Displayed a sourced nonnumeric metric as ordinary text without changing its content.', repaired=True))
    return block


def _validated_blocks(items: list, sources: dict[str, str], *, findings: list[DesignFinding], section_id: str = "", location: str = 'blocks', repair_prose: bool = True) -> list[ReportBlock]:
    if not isinstance(items, list) or not 1 <= len(items) <= 8:
        raise ReportValidationError('Invalid block count', code='invalid_report_structure', location=location)
    blocks = []
    for index, item in enumerate(items):
        with validation_at(f'{location}.{index}'):
            blocks.append(_validated_block(item, sources, findings=findings, section_id=section_id, repair_prose=repair_prose))
    return blocks


def _validated_part(raw: str, chunk: list[tuple[str, str]]) -> ReportDocument:
    payload = _payload(raw)
    payload.pop('tool_request', None)
    sources: dict[str, str] = {}
    for key, text in chunk:
        sources[key] = sources.get(key, '') + ' ' + text
    # Existing clients/revisions with flat blocks remain readable during migration.
    if 'sections' not in payload:
        findings: list[DesignFinding] = []
        blocks = _validated_blocks(payload.get('blocks'), sources, findings=findings, repair_prose=False)
        layouts = {'concept', 'step', 'timeline', 'comparison', 'argument', 'statistic', 'conclusion'}
        types = {b.type for b in blocks}
        selected = blocks[0].type if len(types) == 1 and blocks[0].type in layouts else 'text'
        layout = 'step_flow' if selected == 'step' else selected
        return ReportDocument(sections=[ReportSection(title=blocks[0].title, layout_type=layout, blocks=blocks)], design_review=findings)
    if not isinstance(payload['sections'], list) or not 1 <= len(payload['sections']) <= 8:
        raise ValueError('Invalid section count')
    doc = ReportDocument.model_validate(payload)
    if not doc.title.strip() or len(doc.title) > 80:
        raise ValueError('Report needs a concise topic title')
    # Match the provider schema: up to eight sections, each with up to eight blocks.
    # Editorial brevity is a design preference, not a reason to discard valid sourced content.
    all_ids = list(dict.fromkeys(doc.executive_summary_source_segment_ids))
    seen = set()
    for index, (section, data) in enumerate(zip(doc.sections, payload['sections'])):
        if not section.id.strip() or section.id in seen or not section.title.strip():
            raise ValueError('Sections need unique topic IDs and titles')
        seen.add(section.id)
        section.blocks = _validated_blocks(data.get('blocks'), sources, findings=doc.design_review, section_id=section.id, location=f'sections.{index}.blocks')
        ids = list(dict.fromkeys(key for block in section.blocks for key in block.source_segment_ids))
        with validation_at(f'sections.{index}.title'):
            try:
                _validate_text(section.title, ids, sources)
            except ReportValidationError as exc:
                if exc.code != 'unsupported_report_claim':
                    raise
                candidates = [block.title.strip() for block in section.blocks if block.title.strip()]
                candidates.extend(
                    re.split(r'[，。！？；]', block.text.strip())[0].strip()
                    for block in section.blocks if block.text.strip())
                candidates.append(section.id.strip())
                fallback = next((candidate for candidate in candidates if candidate), '')
                for candidate in candidates:
                    if not candidate:
                        continue
                    try:
                        _validate_text(candidate, ids, sources)
                    except ReportValidationError as fallback_error:
                        if fallback_error.code == 'unsupported_report_claim':
                            continue
                        raise
                    fallback = candidate
                    break
                else:
                    raise
                section.title = fallback
                doc.design_review.append(DesignFinding(
                    section_id=section.id, code='unsupported_heading_removed',
                    message='Replaced a section heading containing an uncited quantity with sourced content.',
                    repaired=True))
        all_ids.extend(ids)
        validate_layout(section)
        if any(f.code == 'metric_presentation_repaired' and f.section_id == section.id for f in doc.design_review):
            section.layout = None  # Recompose for prose instead of retaining a metric-card grid.
        specialized = {'concept': 'concept', 'step_flow': 'step', 'timeline': 'timeline',
                       'comparison': 'comparison', 'argument': 'argument',
                       'statistic': 'statistic', 'conclusion': 'conclusion'}
        expected = specialized.get(section.layout_type)
        kinds = {component_kind(block.type) for block in section.blocks}
        if expected and kinds != {expected}:
            section.layout_type = 'text' if kinds == {'paragraph'} else 'concept' if kinds == {'concept'} else 'mixed'
    if doc.executive_summary.strip():
        try:
            with validation_at('executive_summary'):
                _validate_text(doc.executive_summary, doc.executive_summary_source_segment_ids, sources)
        except ReportValidationError as exc:
            if exc.code != 'unsupported_report_claim':
                raise
            corrected = _supported_prose_sentences(
                doc.executive_summary, doc.executive_summary_source_segment_ids, sources)
            if corrected == doc.executive_summary:
                raise
            doc.executive_summary = corrected
            if not corrected:
                doc.executive_summary_source_segment_ids = []
            doc.design_review.append(DesignFinding(
                section_id='executive_summary', code='unsupported_prose_removed',
                message='Omitted an executive-summary sentence containing a quantity absent from its cited transcript evidence.',
                repaired=True))
            if corrected:
                with validation_at('executive_summary'):
                    _validate_text(corrected, doc.executive_summary_source_segment_ids, sources)
    elif doc.executive_summary_source_segment_ids:
        raise ValueError('Empty summary has sources')
    if doc.title:
        with validation_at('title'):
            try:
                _validate_text(doc.title, all_ids, sources)
            except ReportValidationError as exc:
                if exc.code != 'unsupported_report_claim':
                    raise
                fallback = next((section.title.strip() for section in doc.sections if section.title.strip()), '')
                if fallback:
                    _validate_text(fallback, all_ids, sources)
                    doc.title = fallback
                else:
                    doc.title = ''
                doc.design_review.append(DesignFinding(
                    section_id='title', code='unsupported_heading_removed',
                    message='Replaced a report title containing an uncited quantity with sourced content.',
                    repaired=True))
    return doc


def _same_sources(left: list[str], right: list[str], metadata: dict[str, TranscriptSegment]) -> bool:
    speakers_left = {metadata[key].speaker_id for key in left}
    speakers_right = {metadata[key].speaker_id for key in right}
    return speakers_left == speakers_right and (None not in speakers_left or set(left) == set(right))


def _content_key(block: ReportBlock) -> str:
    data = block.model_dump(exclude={'source_segment_ids'})
    for field in ['options', 'rows', 'data', 'nodes', 'edges', 'cells']:
        for item in data[field]:
            item.pop('source_segment_ids', None)
    # Whitespace differences are not new information; preserve wording and numbers.
    def normalized(value):
        if isinstance(value, str):
            return ' '.join(value.split())
        if isinstance(value, list):
            return [normalized(item) for item in value]
        if isinstance(value, dict):
            return {key: normalized(item) for key, item in value.items()}
        return value
    return json.dumps(normalized(data), sort_keys=True, ensure_ascii=False)


def _deduplicate(doc: ReportDocument, metadata: dict[str, TranscriptSegment]) -> None:
    """Consolidate topic headings and exact content, without guessing semantic equivalence."""
    original = {id(s): [id(b) for b in s.blocks] for s in doc.sections}
    sections: list[ReportSection] = []
    titles: dict[str, ReportSection] = {}
    seen: dict[str, list[ReportBlock]] = {}
    for section in doc.sections:
        title_key = ' '.join(section.title.split()).casefold()
        target = titles.get(title_key) if title_key else None
        if target is None:
            target = section.model_copy(update={'blocks': []})
            original[id(target)] = original[id(section)]
            sections.append(target)
            if title_key:
                titles[title_key] = target
        elif target.layout_type != section.layout_type:
            target.layout_type = 'mixed'
        for block in section.blocks:
            key = _content_key(block)
            duplicate = next((other for other in seen.get(key, [])
                if _same_sources(other.source_segment_ids, block.source_segment_ids, metadata)
                and all(_same_sources(a.source_segment_ids, b.source_segment_ids, metadata)
                        for a, b in zip([*other.options, *other.rows, *other.data, *other.nodes, *other.edges, *other.cells],
                                        [*block.options, *block.rows, *block.data, *block.nodes, *block.edges, *block.cells]))), None)
            if duplicate is not None:
                for left, right in [(duplicate, block), *(pair for field in ['options', 'rows', 'data', 'nodes', 'edges', 'cells']
                                                       for pair in zip(getattr(duplicate, field), getattr(block, field)))]:
                    left.source_segment_ids = list(dict.fromkeys([*left.source_segment_ids, *right.source_segment_ids]))
            else:
                seen.setdefault(key, []).append(block)
                target.blocks.append(block)
    doc.sections = [section for section in sections if section.blocks]
    for section in doc.sections:
        if [id(b) for b in section.blocks] != original.get(id(section)):
            section.layout = None  # Recompose after merges/deletions so references cannot drift.


def generate_report(segments: Sequence[TranscriptSegment], *, language: LanguageCode,
                    client: ChatClient | None = None, max_model_calls: int | None = None) -> ReportDocument:
    chunks = _chunks(segments)
    if not chunks:
        raise ValueError('No transcript available for report generation')
    if client is None:
        raise ReportGenerationError('No summary model is configured. Only the transcript is available.')
    remaining_calls = max_model_calls
    if remaining_calls is not None and (type(remaining_calls) is not int or remaining_calls < len(chunks)):
        raise ValueError('Model-call budget must cover at least one completion per transcript part')
    original_client = client

    class BudgetClient:
        def chat(self, **kwargs):
            nonlocal remaining_calls
            if remaining_calls is not None:
                if remaining_calls <= 0:
                    raise ReportGenerationError('Model-call budget exhausted', code='report_call_budget')
                remaining_calls -= 1
            return original_client.chat(**kwargs)

        @property
        def max_output_tokens(self) -> int:
            return int(getattr(original_client, 'max_output_tokens', 7200))

    budget_client = BudgetClient()
    result = ReportDocument(generation_method='llm')
    topics: dict[str, ReportSection] = {}
    metadata = {segment.id: segment for segment in segments}
    for index, chunk in enumerate(chunks, 1):
        try:
            # Bounded continuity context helps later chunks reuse topic sections.
            outline = [{'id': section.id, 'title': section.title,
                        'covered': [{'type': b.type, 'title': b.title, 'text': b.text[:240],
                                     'speakers': list(dict.fromkeys(metadata[key].speaker_id for key in b.source_segment_ids)),
                                     'options': [{'label': o.label, 'text': o.text[:120]} for o in b.options],
                                     'columns': b.columns, 'rows': [r.cells for r in b.rows],
                                     'visuals': {field: [item.model_dump(exclude={'source_segment_ids'}) for item in getattr(b, field)]
                                                 for field in ['data', 'nodes', 'edges', 'cells'] if getattr(b, field)}}
                                    for b in section.blocks][-4:]}
                       for section in result.sections][-12:]
            while len(json.dumps(outline, ensure_ascii=False)) > 10000 and outline:
                outline.pop(0)
            evidence = {'part': index, 'previous_outline': outline,
                        'previous_summary': result.executive_summary[-1200:], 'transcript': [
                {'id': key, 'text': text, 'speaker_id': metadata[key].speaker_id,
                 'start_time': metadata[key].start_time, 'end_time': metadata[key].end_time}
                for key, text in chunk]}
            def validate_candidate(raw, current_chunk):
                part = _validated_part(raw, current_chunk)
                for section_index, section in enumerate(part.sections):
                    for block_index, block in enumerate(section.blocks):
                        if block.speaker_id and any(metadata[key].speaker_id != block.speaker_id for key in block.source_segment_ids):
                            raise ReportGenerationError(
                                'The model assigned a speaker ID that is absent from the cited transcript metadata.',
                                code='invalid_report_speaker',
                                location=f'parts.{index - 1}.sections.{section_index}.blocks.{block_index}.speaker_id')
                return part

            # Reserve one completion for every later chunk before offering optional tool rounds.
            part_budget = None if remaining_calls is None else remaining_calls - (len(chunks) - index)
            part = VisualDesignAgent(budget_client, validate_candidate, max_turns=part_budget).run(evidence, chunk, _LANGUAGES[language])
            result.design_review.extend(part.design_review)
            if not result.title:
                result.title = part.title
            if part.executive_summary and part.executive_summary not in result.executive_summary:
                result.executive_summary = '\n\n'.join(filter(None, [result.executive_summary, part.executive_summary]))
                result.executive_summary_source_segment_ids = list(dict.fromkeys([
                    *result.executive_summary_source_segment_ids, *part.executive_summary_source_segment_ids]))
            for position, section in enumerate(part.sections):
                topic_id = section.id or f'part-{index}-{position}'
                if topic_id not in topics:
                    section.id = topic_id
                    topics[topic_id] = section
                    result.sections.append(section)
                else:
                    target = topics[topic_id]
                    target.blocks.extend(section.blocks)
                    target.layout = None
                    if target.layout_type != section.layout_type:
                        target.layout_type = 'mixed'
            _deduplicate(result, metadata)
            topics = {section.id: section for section in result.sections}
        except LLMResponseError as exc:
            _LOGGER.warning("Report generation failed part=%s code=%s", index, exc.code)
            raise ReportGenerationError(f'Part {index}: {exc}', code=exc.code) from exc
        except ReportValidationError as exc:
            location = '.'.join(filter(None, [f'parts.{index - 1}', exc.location]))
            _LOGGER.warning('Report generation rejected code=%s location=%s', exc.code, location)
            raise ReportGenerationError(str(exc), code=exc.code, location=location) from exc
        except ReportGenerationError as exc:
            _LOGGER.warning("Report generation failed part=%s code=%s", index, exc.code)
            raise
        except json.JSONDecodeError as exc:
            _LOGGER.warning("Report generation rejected part=%s code=invalid_report_json line=%s column=%s", index, exc.lineno, exc.colno)
            raise ReportGenerationError(
                f'Part {index}: the model returned invalid report JSON. No summary was published.',
                code='invalid_report_json') from exc
        except ValidationError as exc:
            locations = [structure_location(exc)]
            _LOGGER.warning("Report generation rejected part=%s code=invalid_report_structure fields=%s", index, locations[:8])
            raise ReportGenerationError(
                f'Part {index}: the model returned an invalid report structure. No summary was published.',
                code='invalid_report_structure', location=f'parts.{index - 1}.{locations[0]}') from exc
        except TimeoutError as exc:
            _LOGGER.warning("Report generation failed part=%s code=model_timeout", index)
            raise ReportGenerationError(
                f'Part {index}: the model request timed out. No summary was published.', code='model_timeout') from exc
        except Exception as exc:
            # Do not log model content, credentials or arbitrary provider exception bodies.
            reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            _LOGGER.warning("Report generation rejected part=%s error=%s reason=%s", index, type(exc).__name__, reason[:240])
            raise ReportGenerationError(
                f'Part {index}: model report generation failed ({type(exc).__name__}). '
                'Only the transcript is available; retry after checking the summary model.'
            ) from exc
    if not result.title:
        result.title = result.sections[0].title or {'zh': '會議報告', 'en': 'Meeting report', 'de': 'Besprechungsbericht'}[language]
    result.design_review.extend(review_design(result, repair=True))
    if any(finding.code in {'unsupported_prose_removed', 'unsupported_heading_removed'} for finding in result.design_review):
        result.warnings.append('Some unsupported model claims were omitted. Check the linked transcript sources.')
    if any(finding.code == 'metric_presentation_repaired' for finding in result.design_review):
        result.warnings.append('Some metric cards were displayed as ordinary text because they contain no numeric values. Text and sources were preserved.')
    if any(finding.code == 'chart_presentation_repaired' for finding in result.design_review):
        result.warnings.append('A one-point chart was displayed as a sourced metric card because a comparison chart needs at least two observations.')
    # The hero orients the reader; body blocks are no longer copied into it.
    return result
