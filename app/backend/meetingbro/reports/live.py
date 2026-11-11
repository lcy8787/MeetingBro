"""Build incremental report revisions from existing, saved summary snapshots."""
from datetime import datetime, timezone
import uuid

from ..schemas import ReportBlock, ReportDocument, ReportRevision, ReportSection, SummarySnapshot
from ..storage.db import Storage

_LABELS = {'rolling_summary': 'Recent summary', 'cumulative_meeting_summary': 'Meeting overview', 'final_summary': 'Final summary'}


def update_live_report(storage: Storage, snapshot: SummarySnapshot) -> ReportRevision | None:
    label = _LABELS.get(snapshot.summary_type)
    if not label or not snapshot.content.strip() or snapshot.generation_method != 'llm':
        return None
    segment_ids = {s.id for s in storage.list_segments(snapshot.meeting_id)}
    sources = [source for source in snapshot.source_segment_ids if source in segment_ids]
    # Do not publish a summary with no persisted transcript source.
    if not sources:
        return None
    report_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'meetingbro:live-report:{snapshot.meeting_id}'))
    revisions = [r for r in storage.list_report_revisions(snapshot.meeting_id) if r.report_id == report_id]
    previous = revisions[-1] if revisions else None
    sections = []
    if previous and previous.document.generation_method == 'llm' and snapshot.summary_type != "final_summary":
        for old in previous.document.sections:
            blocks = [block.model_copy(update={'source_segment_ids': [s for s in block.source_segment_ids if s in segment_ids]})
                      for block in old.blocks]
            blocks = [block for block in blocks if block.source_segment_ids]
            if blocks:
                sections.append(old.model_copy(update={'blocks': blocks}))
    section = ReportSection(id=snapshot.summary_type, title=label,
                            blocks=[ReportBlock(text=snapshot.content, source_segment_ids=sources)])
    index = next((i for i, item in enumerate(sections) if item.id == section.id), None)
    if index is None:
        sections.append(section)
    else:
        sections[index] = section
    document = ReportDocument(title='Meeting report', sections=sections, generation_method='llm',
        warnings=['Summaries can contain errors. Check the linked transcript sources.'])
    result = ReportRevision(report_id=report_id, meeting_id=snapshot.meeting_id, report_type='live',
                            revision=previous.revision + 1 if previous else 1,
                            document=document, created_at=datetime.now(timezone.utc))
    storage.insert_report_revision(result)
    return result
