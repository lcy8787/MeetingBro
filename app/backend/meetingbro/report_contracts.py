"""Shared, finite vocabulary for source-grounded visual reports (no executable UI)."""
from typing import Literal

ComponentType = Literal[
    'paragraph', 'bullet', 'quote', 'action', 'concept', 'step', 'timeline', 'comparison',
    'argument', 'statistic', 'conclusion', 'table', 'pros_cons', 'insight',
    'HeroSection', 'SectionHeader', 'EditorialText', 'SummaryCard', 'InsightCallout',
    'ConclusionPanel', 'ComparisonCards', 'ComparisonMatrix', 'ProsCons', 'PerspectiveCards',
    'SpecificationTable', 'Timeline', 'Stepper', 'ProcessFlow', 'CausalFlow', 'KeyMetrics',
    'DataBars', 'BarChart', 'LineChart', 'DataTable', 'DecisionPanel', 'ActionItems',
    'OpenQuestions', 'SpeakerQuote', 'TranscriptReference', 'Accordion', 'TabGroup',
    'ExpandableDetails', 'KnowledgeGraph', 'ArgumentMap', 'DecisionTree', 'RiskMatrix',
    'Heatmap', 'SankeyDiagram', 'InteractiveChart',
]
ALIASES = dict(zip(
    ['HeroSection', 'SectionHeader', 'EditorialText', 'SummaryCard', 'InsightCallout',
     'ConclusionPanel', 'ComparisonCards', 'ComparisonMatrix', 'ProsCons', 'PerspectiveCards',
     'SpecificationTable', 'Timeline', 'Stepper', 'ProcessFlow', 'KeyMetrics', 'DataTable',
     'DecisionPanel', 'ActionItems', 'OpenQuestions', 'SpeakerQuote', 'TranscriptReference'],
    ['hero', 'heading', 'paragraph', 'concept', 'insight', 'conclusion', 'comparison', 'table',
     'pros_cons', 'comparison', 'table', 'timeline', 'step', 'step', 'statistic', 'table',
     'decision', 'action', 'questions', 'quote', 'reference']))
TABLE_TYPES = {'table', 'ComparisonMatrix', 'SpecificationTable', 'DataTable'}
CHART_TYPES = {'DataBars', 'BarChart', 'LineChart', 'InteractiveChart'}
GRAPH_TYPES = {'KnowledgeGraph', 'ArgumentMap', 'DecisionTree', 'CausalFlow', 'SankeyDiagram'}
MATRIX_TYPES = {'RiskMatrix', 'Heatmap'}


def component_kind(name: str) -> str:
    return ALIASES.get(name, name)
