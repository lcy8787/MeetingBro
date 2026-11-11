"""Generate reports in bounded transcript chunks with explicit provenance.

Citations and numeric checks reject some unsupported claims; they cannot prove
semantic correctness. A failed generation never becomes a transcript report.
"""
from __future__ import annotations

import json
import re
from typing import Protocol, Sequence

from ..llm.openai_compatible import LLMResponseError
from ..schemas import LanguageCode, ReportBlock, ReportDocument, ReportSection, TranscriptSegment
from .components import REPORT_SCHEMA as _REPORT_SCHEMA, planning_prompt


class ChatClient(Protocol):
    def chat(self, *, system: str, user: str, max_tokens: int, temperature: float = 0.2,
             response_schema: dict | None = None) -> str: ...


class ReportGenerationError(RuntimeError):
    """A model did not produce a complete, validated report."""

    def __init__(self, message: str, *, code: str = 'invalid_report') -> None:
        super().__init__(message)
        self.code = code


_NUMBERS = re.compile(r"(?<![A-Za-z0-9_])[-+−]?\d+(?:[.,]\d+)*(?:%|％)?(?![A-Za-z0-9_])")
_CHINESE_NUMBERS = re.compile(
    r"(?:[零〇一二兩两三四五六七八九十百千萬万億亿兆]{2,}(?:[點点][零〇一二兩两三四五六七八九]+)?"
    r"|[零〇一二兩两三四五六七八九][點点][零〇一二兩两三四五六七八九]+"
    r"|[十百千萬万億亿兆]"
    r"|[零〇一二兩两三四五六七八九](?=個|人|台|次|年|月|日|度|克|元|吋|寸|尼特|赫茲|分鐘|秒))"
)
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
    content = raw.strip()
    if content.startswith('```'):
        content = re.sub(r'^```(?:json)?\s*|\s*```$', '', content, flags=re.IGNORECASE)
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError('Invalid report response')
    return payload


def _validate_text(text: str, ids: list[str], sources: dict[str, str]) -> None:
    if not ids or any(key not in sources for key in ids):
        raise ValueError('Missing or unknown transcript sources')
    evidence = ' '.join(sources[key] for key in ids)
    if not set(_NUMBERS.findall(text)) <= set(_NUMBERS.findall(evidence)):
        raise ValueError('Unsupported number')
    if not set(_CHINESE_NUMBERS.findall(text)) <= set(_CHINESE_NUMBERS.findall(evidence)):
        raise ValueError('Unsupported Chinese number')


def _validated_blocks(items: list, sources: dict[str, str]) -> list[ReportBlock]:
    if not isinstance(items, list) or not 1 <= len(items) <= 8:
        raise ValueError('Invalid block count')
    blocks = []
    for item in items:
        block = ReportBlock.model_validate(item)
        if not block.text.strip():
            raise ValueError('A report block has no text')
        visible = ' '.join((block.title, block.text, block.detail, *block.columns))
        _validate_text(visible, block.source_segment_ids, sources)
        if block.type == 'statistic':
            values = visible + ' ' + ' '.join(o.label + ' ' + o.text for o in block.options)
            if not _NUMBERS.search(values) and not _CHINESE_NUMBERS.search(values):
                raise ValueError('Statistic has no verifiable numeric value')
        for option in block.options:
            if not option.label.strip() or not option.text.strip():
                raise ValueError('Interactive option has no label or explanation')
            _validate_text(option.label + ' ' + option.text, option.source_segment_ids, sources)
        if block.type == 'table':
            if len(block.columns) < 2 or len(block.rows) < 2 or block.options:
                raise ValueError('Table needs comparable columns and rows')
            if any(not column.strip() for column in block.columns):
                raise ValueError('Empty table heading')
            for row in block.rows:
                if len(row.cells) != len(block.columns) or any(not cell.strip() for cell in row.cells):
                    raise ValueError('Invalid table row')
                _validate_text(' '.join(row.cells), row.source_segment_ids, sources)
        elif block.columns or block.rows:
            raise ValueError('Only tables may contain rows or columns')
        blocks.append(block)
    return blocks


def _validated_part(raw: str, chunk: list[tuple[str, str]]) -> ReportDocument:
    payload = _payload(raw)
    sources: dict[str, str] = {}
    for key, text in chunk:
        sources[key] = sources.get(key, '') + ' ' + text
    # Existing clients/revisions with flat blocks remain readable during migration.
    if 'sections' not in payload:
        blocks = _validated_blocks(payload.get('blocks'), sources)
        layouts = {'concept', 'step', 'timeline', 'comparison', 'argument', 'statistic', 'conclusion'}
        types = {b.type for b in blocks}
        selected = blocks[0].type if len(types) == 1 and blocks[0].type in layouts else 'text'
        layout = 'step_flow' if selected == 'step' else selected
        return ReportDocument(sections=[ReportSection(title=blocks[0].title, layout_type=layout, blocks=blocks)])
    if not isinstance(payload['sections'], list) or not 1 <= len(payload['sections']) <= 8:
        raise ValueError('Invalid section count')
    doc = ReportDocument.model_validate(payload)
    if not doc.title.strip() or len(doc.title) > 80:
        raise ValueError('Report needs a concise topic title')
    if sum(len(section.blocks) for section in doc.sections) > 12:
        raise ValueError('Too many report blocks in one part')
    all_ids = []
    seen = set()
    for section, data in zip(doc.sections, payload['sections']):
        if not section.id.strip() or section.id in seen or not section.title.strip():
            raise ValueError('Sections need unique topic IDs and titles')
        seen.add(section.id)
        section.blocks = _validated_blocks(data.get('blocks'), sources)
        ids = list(dict.fromkeys(key for block in section.blocks for key in block.source_segment_ids))
        _validate_text(section.title, ids, sources)
        all_ids.extend(ids)
        specialized = {'concept': 'concept', 'step_flow': 'step', 'timeline': 'timeline',
                       'comparison': 'comparison', 'argument': 'argument',
                       'statistic': 'statistic', 'conclusion': 'conclusion'}
        expected = specialized.get(section.layout_type)
        if expected and any(block.type != expected for block in section.blocks):
            section.layout_type = 'mixed'
    if doc.executive_summary.strip():
        _validate_text(doc.executive_summary, doc.executive_summary_source_segment_ids, sources)
    elif doc.executive_summary_source_segment_ids:
        raise ValueError('Empty summary has sources')
    if doc.title:
        _validate_text(doc.title, all_ids, sources)
    return doc


def _same_sources(left: list[str], right: list[str], metadata: dict[str, TranscriptSegment]) -> bool:
    speakers_left = {metadata[key].speaker_id for key in left}
    speakers_right = {metadata[key].speaker_id for key in right}
    return speakers_left == speakers_right and (None not in speakers_left or set(left) == set(right))


def _content_key(block: ReportBlock) -> str:
    data = block.model_dump(exclude={'source_segment_ids'})
    for item in [*data['options'], *data['rows']]:
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
    sections: list[ReportSection] = []
    titles: dict[str, ReportSection] = {}
    seen: dict[str, list[ReportBlock]] = {}
    for section in doc.sections:
        title_key = ' '.join(section.title.split()).casefold()
        target = titles.get(title_key) if title_key else None
        if target is None:
            target = section.model_copy(update={'blocks': []})
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
                        for a, b in zip([*other.options, *other.rows], [*block.options, *block.rows]))), None)
            if duplicate is not None:
                for left, right in [(duplicate, block), *zip(duplicate.options, block.options), *zip(duplicate.rows, block.rows)]:
                    left.source_segment_ids = list(dict.fromkeys([*left.source_segment_ids, *right.source_segment_ids]))
            else:
                seen.setdefault(key, []).append(block)
                target.blocks.append(block)
    doc.sections = [section for section in sections if section.blocks]


def generate_report(segments: Sequence[TranscriptSegment], *, language: LanguageCode,
                    client: ChatClient | None = None) -> ReportDocument:
    chunks = _chunks(segments)
    if not chunks:
        raise ValueError('No transcript available for report generation')
    if client is None:
        raise ReportGenerationError('No summary model is configured. Only the transcript is available.')
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
                                     'columns': b.columns, 'rows': [r.cells for r in b.rows]}
                                    for b in section.blocks][-4:]}
                       for section in result.sections][-12:]
            while len(json.dumps(outline, ensure_ascii=False)) > 10000 and outline:
                outline.pop(0)
            evidence = {'part': index, 'previous_outline': outline,
                        'previous_summary': result.executive_summary[-1200:], 'transcript': [
                {'id': key, 'text': text, 'speaker_id': metadata[key].speaker_id,
                 'start_time': metadata[key].start_time, 'end_time': metadata[key].end_time}
                for key, text in chunk]}
            raw = client.chat(system=planning_prompt(_LANGUAGES[language]),
                              user=json.dumps(evidence, ensure_ascii=False), max_tokens=4800,
                              temperature=0.1, response_schema=_REPORT_SCHEMA)
            # Invalid output fails without a hidden paid repair request.
            part = _validated_part(raw, chunk)
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
                    if target.layout_type != section.layout_type:
                        target.layout_type = 'mixed'
            _deduplicate(result, metadata)
            topics = {section.id: section for section in result.sections}
        except LLMResponseError as exc:
            raise ReportGenerationError(f'Part {index}: {exc}', code=exc.code) from exc
        except Exception as exc:
            raise ReportGenerationError(
                f'Part {index}: model report generation failed ({type(exc).__name__}). '
                'Only the transcript is available; retry after checking the summary model.'
            ) from exc
    if not result.title:
        result.title = result.sections[0].title or {'zh': '會議報告', 'en': 'Meeting report', 'de': 'Besprechungsbericht'}[language]
    # The hero orients the reader; body blocks are no longer copied into it.
    return result
