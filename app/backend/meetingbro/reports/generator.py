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
from ..schemas import DesignFinding, LanguageCode, MissingReportPart, ReportBlock, ReportDocument, ReportOption, ReportSection, TranscriptSegment
from ..report_contracts import CHART_TYPES, TABLE_TYPES, component_kind
from .design import validate_layout, review_design
from .evidence import numeric, validate_component_fields, validate_visual
from .relations import SCHEMA, compose_item, prompt
from .errors import ReportValidationError, structure_location, validation_at


_LOGGER = logging.getLogger(__name__)


class ChatClient(Protocol):
    def chat(self, *, system: str, user: str, max_tokens: int, temperature: float = 0.2,
             response_schema: dict | None = None, single_attempt: bool = False) -> str: ...


class ReportGenerationError(RuntimeError):
    """A model did not produce a complete, validated report."""

    def __init__(self, message: str, *, code: str = 'invalid_report', location: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.location = location


from .quantities import ARABIC_NUMBERS as _NUMBERS, CHINESE_NUMBERS as _CHINESE_NUMBERS, number_tokens
_LANGUAGES = {"en": "English", "zh": "Traditional Chinese", "de": "German"}


_GAP_REASONS = {
    'output_truncated': ('Output limit reached', '回答達到長度上限', 'Ausgabelimit erreicht'),
    'ambiguous_response': ('Multiple answers returned', '回答含多份 JSON', 'Mehrere Antworten erhalten'),
    'empty_response': ('No final answer', '模型未提供最終回答', 'Keine endgültige Antwort'),
    'refusal': ('Model declined', '模型拒絕回答', 'Modell hat abgelehnt'),
    'model_timeout': ('Request timed out', '請求逾時', 'Zeitüberschreitung'),
    'model_connection': ('Connection failed', '無法連接模型', 'Verbindung fehlgeschlagen'),
    'model_rate_limited': ('Rate limited', '服務限制請求頻率', 'Anfragelimit erreicht'),
    'invalid_report_json': ('Incomplete or invalid JSON', 'JSON 不完整或格式錯誤', 'Unvollständiges oder ungültiges JSON'),
    'invalid_report_evidence': ('Evidence could not be verified', '無法核實引用證據', 'Belege nicht überprüfbar'),
    'invalid_report_sources': ('Unknown transcript sources', '找不到逐字稿來源', 'Unbekannte Transkriptquelle'),
    'unsupported_report_claim': ('Unsupported claim', '引用未能支持內容', 'Unbelegte Aussage'),
    'invalid_report_speaker': ('Unknown speaker', '無法核實講者', 'Unbekannter Sprecher'),
    'invalid_report_structure': ('Invalid report structure', '摘要結構有誤', 'Ungültige Berichtsstruktur'),
    'structured_output_unsupported': ('Model rejected the response format', '模型不支援回答格式', 'Antwortformat abgelehnt'),
}


def _coverage_notice(doc, language):
    labels = {'en': 'Partial summary · Missing recording intervals', 'zh': '部分摘要 · 未涵蓋的錄音時段',
              'de': 'Teilzusammenfassung · Fehlende Aufnahmeabschnitte'}
    notices = [labels[language]]
    def clock(seconds):
        minutes, seconds = divmod(max(0, int(seconds)), 60)
        return f'{minutes:02d}:{seconds:02d}'
    for gap in doc.missing_parts:
        messages = _GAP_REASONS.get(gap.reason)
        reason = messages[('en', 'zh', 'de').index(language)] if messages else gap.reason
        notices.append(f'{clock(gap.start_time)}–{clock(gap.end_time)} ({reason})')
    return ' · '.join(notices)


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
    # Structured components carry their payload below the heading. Avoid a
    # second identical line when the introductory claim was omitted locally.
    if (block.options or block.data) and block.title and block.text == block.title:
        block.text = ''
    return block


def _omission(section_id):
    return DesignFinding(section_id=section_id, code='content_omitted',
                         message='Some content could not be verified.', repaired=True)


def _chart_excerpt(item, sources):
    text, ids = item.get('text'), item.get('source_segment_ids')
    if not isinstance(text, str) or not isinstance(ids, list) or not all(isinstance(k, str) and k in sources for k in ids):
        return None
    if not text.strip():
        return None
    # A matching word inside a failed chart's labels is not independent prose.
    sentences = (sentence.strip() for key in ids for sentence in
                 re.split(r'(?<=[.!?。！？])\s*|\n+', sources[key]))
    if text.strip() not in sentences:
        return None
    return ReportBlock(type='EditorialText', text=text, source_segment_ids=ids)


def _validated_part(raw, chunk, metadata, part_number, language):
    payload = _payload(raw)
    if payload.pop('tool_request', None):
        raise ValueError('Expected final report content')
    if set(payload) - {'title', 'executive_summary', 'executive_summary_source_segment_ids', 'sections', 'blocks'}:
        raise ValueError('Unknown report fields')
    sources = {}
    for key, text in chunk:
        sources[key] = sources.get(key, '') + ' ' + text
    speakers = {key: metadata[key].speaker_id for key in sources}
    legacy = 'sections' not in payload
    sections = [{'id': '', 'title': '', 'blocks': payload.get('blocks')}] if legacy else payload['sections']
    if not isinstance(sections, list) or not 1 <= len(sections) <= 8:
        raise ValueError('Invalid section count')
    doc = ReportDocument()
    title = payload.get('title')
    if isinstance(title, str):
        doc.title = title
    summary, summary_ids = payload.get('executive_summary', ''), payload.get('executive_summary_source_segment_ids', [])
    if isinstance(summary, str) and isinstance(summary_ids, list) and all(isinstance(key, str) for key in summary_ids):
        doc.executive_summary = summary
        doc.executive_summary_source_segment_ids = summary_ids
    else:
        doc.design_review.append(_omission('executive_summary'))
    if not legacy and (not doc.title.strip() or len(doc.title) > 80):
        doc.title = ''
    all_ids = []
    seen = set()
    last_error = None
    for index, data in enumerate(sections):
        location = f'sections.{index}'
        section_id = f'part-{part_number}-section-{index + 1}'
        try:
            with validation_at(location):
                if not isinstance(data, dict) or ('items' in data) == ('blocks' in data):
                    raise ValueError('Expected section content')
                semantic = 'items' in data
                field = 'items' if semantic else 'blocks'
                section = ReportSection.model_validate({k: v for k, v in data.items() if k != field})
                section.id = section.id.strip() or section_id
                section_id = section.id
                if section.id in seen:
                    raise ValueError('Duplicate section')
                items = data[field]
                if not isinstance(items, list) or not 1 <= len(items) <= 8:
                    raise ValueError('Invalid item count')
        except ReportValidationError as exc:
            last_error = exc
            doc.design_review.append(_omission(section_id))
            continue
        omitted = False
        for position, item in enumerate(items):
            findings = []
            try:
                with validation_at(f'{location}.blocks.{position}'):
                    candidates = compose_item(item, sources, speakers, findings, section.id) if semantic else [item]
                    checked = []
                    for candidate in candidates:
                        block = _validated_block(candidate, sources, findings=findings,
                            section_id=section.id, repair_prose=not legacy)
                        if block.speaker_id and any(speakers[key] != block.speaker_id for key in block.source_segment_ids):
                            raise ReportValidationError('Unknown speaker ID', code='invalid_report_speaker', location='speaker_id')
                        checked.append(block)
                section.blocks.extend(checked)
                doc.design_review.extend(findings)
            except (KeyError, TypeError, AttributeError, ReportValidationError) as exc:
                last_error = exc if isinstance(exc, ReportValidationError) else ReportValidationError(
                    'Invalid content item', code='invalid_report_structure', location=f'{location}.blocks.{position}')
                omitted = True
                # Only retain independently sourced narrative. A broken chart's
                # numbers, conclusions, or claims cannot be rescued by relabelling it.
                if isinstance(item, dict) and (item.get('kind') == 'measurements' or isinstance(item.get('type'), str) and item['type'] in CHART_TYPES):
                    fallback = _chart_excerpt(item, sources)
                    if fallback:
                        section.blocks.append(fallback)
        if omitted:
            doc.design_review.append(_omission(section.id))
            section.layout = None
        if not section.blocks:
            continue
        seen.add(section.id)
        doc.sections.append(section)
        ids = list(dict.fromkeys(key for block in section.blocks for key in block.source_segment_ids))
        if not section.title.strip() and not legacy:
            section.title = section.blocks[0].title or section.blocks[0].text[:80]
        if legacy:
            section.title = section.blocks[0].title
            kinds = {block.type for block in section.blocks}
            if len(kinds) == 1 and section.blocks[0].type in ('step','timeline','concept','comparison','argument','statistic','conclusion'):
                section.layout_type = 'step_flow' if section.blocks[0].type == 'step' else section.blocks[0].type
        elif semantic:
            section.layout_type = 'mixed'
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
    if not doc.sections:
        raise last_error or ValueError('No usable report content')
    # Avoid repeating an omitted claim in the report's overview.
    if any(f.code == 'content_omitted' for f in doc.design_review):
        doc.executive_summary, doc.executive_summary_source_segment_ids = '', []
        names = [section.title for section in doc.sections if section.title.strip()]
        separator = '、' if language == 'zh' else ', '
        if not doc.title:
            doc.title = names[0] if names else ''
        if names:
            doc.executive_summary = {'zh': '本次討論涵蓋', 'en': 'Topics covered include ',
                                     'de': 'Behandelte Themen sind '}[language] + separator.join(names[:3]) + ('。' if language == 'zh' else '.')
            doc.executive_summary_source_segment_ids = list(dict.fromkeys(
                key for section in doc.sections if section.title in names[:3]
                for block in section.blocks for key in block.source_segment_ids))
    if (doc.executive_summary and (not doc.executive_summary_source_segment_ids
            or not set(doc.executive_summary_source_segment_ids) <= sources.keys())
            or not doc.executive_summary and doc.executive_summary_source_segment_ids):
        doc.executive_summary, doc.executive_summary_source_segment_ids = '', []
        doc.design_review.append(_omission('executive_summary'))
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
                corrected = ''
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
    all_ids.extend(doc.executive_summary_source_segment_ids)
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
    if max_model_calls is not None and (type(max_model_calls) is not int or max_model_calls < len(chunks)):
        raise ValueError('Model-call budget must cover at least one completion per transcript part')
    last_error = None
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
            raw = client.chat(system=prompt(_LANGUAGES[language]), user=json.dumps(evidence, ensure_ascii=False),
                              max_tokens=getattr(client, 'max_output_tokens', 7200), temperature=0.1,
                              response_schema=SCHEMA, single_attempt=True)
            part = _validated_part(raw, chunk, metadata, index, language)
            result.design_review.extend(part.design_review)
            if not result.title:
                result.title = part.title
            if not result.executive_summary and part.executive_summary:
                result.executive_summary = part.executive_summary
                result.executive_summary_source_segment_ids = part.executive_summary_source_segment_ids
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
        except (LLMResponseError, ValueError, RuntimeError, TimeoutError) as exc:
            code = 'invalid_report_structure'
            location = f'parts.{index - 1}'
            if isinstance(exc, (LLMResponseError, ReportValidationError, ReportGenerationError)):
                code = exc.code
                if getattr(exc, 'location', None):
                    location += '.' + exc.location
            elif isinstance(exc, json.JSONDecodeError):
                code = 'invalid_report_json'
            elif isinstance(exc, TimeoutError):
                code = 'model_timeout'
            elif isinstance(exc, ValidationError):
                location += '.' + structure_location(exc)
            elif isinstance(exc, RuntimeError):
                code = 'provider_error'
            _LOGGER.warning('Report generation failed code=%s location=%s', code, location)
            last_error = ReportGenerationError(f'Part {index}: {code}', code=code, location=location)
            result.missing_parts.append(MissingReportPart(part=index, reason=code,
                start_time=min(metadata[key].start_time for key, _ in chunk),
                end_time=max(metadata[key].end_time for key, _ in chunk)))
    if not result.sections:
        raise last_error or ReportGenerationError('No usable report content')
    if result.missing_parts:
        result.coverage = 'partial'
        result.warnings.append(_coverage_notice(result, language))
    titles = {s.id: s.title for s in result.sections}
    omitted = dict.fromkeys(titles.get(f.section_id, f.section_id) for f in result.design_review if f.code == 'content_omitted')
    if omitted:
        label = {'en': 'Unverified content omitted from', 'zh': '已略去未能核實的內容', 'de': 'Unbelegte Inhalte ausgelassen'}[language]
        result.warnings.append(f"{label} · " + ', '.join(omitted))
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
