"""Safe report diagnostics: locations contain field names and indices, never content."""
from contextlib import contextmanager

from pydantic import ValidationError


class ReportValidationError(ValueError):
    def __init__(self, message: str, *, code: str, location: str | None = None):
        super().__init__(message)
        self.code = code
        self.location = location


def structure_location(exc: ValidationError) -> str:
    # Pydantic locations for forbidden extra keys can contain model-generated text.
    allowed = set('title executive_summary executive_summary_source_segment_ids sections id layout_type blocks layout type text detail options columns rows data nodes edges cells speaker_id evidence_quote source_segment_ids label value unit series source target relation row column op block_index span emphasis children takeaways generation_method warnings design_review'.split())
    location = exc.errors(include_input=False, include_context=False)[0]['loc']
    return '.'.join(str(item) if type(item) is int or item in allowed else 'field' for item in location)


@contextmanager
def validation_at(location: str):
    try:
        yield
    except ReportValidationError as exc:
        exc.location = '.'.join(filter(None, [location, exc.location]))
        raise
    except ValidationError as exc:
        raise ReportValidationError('The model returned an invalid report structure.',
            code='invalid_report_structure', location='.'.join(filter(None, [location, structure_location(exc)]))) from exc
    except ValueError as exc:
        raise ReportValidationError('The model returned an invalid report structure.',
            code='invalid_report_structure', location=location) from exc
