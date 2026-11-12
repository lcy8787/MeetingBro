"""Real-browser verification for the exported standalone report.

Skipped when Google Chrome is not installed. The test bypasses the LLM and
renders fixed validated data, so only the renderer and its native JavaScript
are under test.
"""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request

import pytest

from meetingbro.reports.standalone import write_standalone_report
from meetingbro.schemas import (ReportBlock, ReportDatum, ReportDocument, ReportGraphEdge, ReportGraphNode,
                                 ReportOption, ReportRevision, ReportSection, TranscriptSegment)

CHROME_CANDIDATES = [
    os.environ.get("CHROME_BIN"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    shutil.which("google-chrome"),
    shutil.which("chromium"),
    shutil.which("chromium-browser"),
]


def chrome_binary():
    return next((path for path in CHROME_CANDIDATES if path and Path(path).exists()), None)


def sample_report():
    now = datetime.now(timezone.utc)
    transcript = [
        TranscriptSegment(id='s1', meeting_id='m', text='We agree to launch on Tuesday. Alice will review the plan.',
                          start_time=0, end_time=10, original_language='en', confidence=1, created_at=now),
        TranscriptSegment(id='s2', meeting_id='m', text='January cost 12 USD. February cost 6 USD.',
                          start_time=10, end_time=20, original_language='en', confidence=1, created_at=now),
    ]
    blocks = [
        ReportSection(id='overview', title='Overview', layout_type='text', blocks=[
            ReportBlock(type='SummaryCard', title='Decision', text='We agree to launch on Tuesday.',
                        source_segment_ids=['s1'], options=[
                            ReportOption(label='Launch', text='We agree to launch on Tuesday.', source_segment_ids=['s1'])]),
            ReportBlock(type='Timeline', title='Flow', text='Meeting flow', source_segment_ids=['s1'], options=[
                ReportOption(label='Review', text='Alice will review the plan.', source_segment_ids=['s1']),
                ReportOption(label='Launch', text='We agree to launch on Tuesday.', source_segment_ids=['s1']),
            ]),
            ReportBlock(type='ActionItems', title='Tasks', text='Assigned work.', source_segment_ids=['s1'], options=[
                ReportOption(label='Review', text='Alice will review the plan.', source_segment_ids=['s1'])]),
            ReportBlock(type='DecisionPanel', title='Decision', text='Agreed decision.', source_segment_ids=['s1'], options=[
                ReportOption(label='Launch', text='We agree to launch on Tuesday.', source_segment_ids=['s1'])]),
        ]),
        ReportSection(id='details', title='Details', layout_type='mixed', blocks=[
            ReportBlock(type='Accordion', title='Background', text='Supporting context.', source_segment_ids=['s1'], options=[
                ReportOption(label='More context', text='Alice will review the plan.', source_segment_ids=['s1']),
                ReportOption(label='Decision context', text='We agree to launch on Tuesday.', source_segment_ids=['s1'])]),
            ReportBlock(type='TabGroup', title='Views', text='Choose a view.', source_segment_ids=['s1'], options=[
                ReportOption(label='Plan', text='Alice will review the plan.', source_segment_ids=['s1']),
                ReportOption(label='Decision', text='We agree to launch on Tuesday.', source_segment_ids=['s1'])]),
            ReportBlock(type='DataTable', title='Table', text='Structured details.', source_segment_ids=['s1'],
                        columns=['Item', 'Owner'], rows=[
                            {'cells': ['Plan', 'Alice'], 'source_segment_ids': ['s1']},
                            {'cells': ['Launch', 'Team'], 'source_segment_ids': ['s1']}]),
            ReportBlock(type='OpenQuestions', title='Questions', text='Open questions.', source_segment_ids=['s1'], options=[
                ReportOption(label='Timeline', text='We agree to launch on Tuesday.', source_segment_ids=['s1'])]),
        ]),
        ReportSection(id='visuals', title='Visuals', layout_type='statistic', blocks=[
            ReportBlock(type='BarChart', title='Costs', text='Monthly costs.', source_segment_ids=['s2'], data=[
                ReportDatum(label='January', value='12', unit='USD', series='cost', evidence_quote='January cost 12 USD.', source_segment_ids=['s2']),
                ReportDatum(label='February', value='6', unit='USD', series='cost', evidence_quote='February cost 6 USD.', source_segment_ids=['s2']),
            ]),
            ReportBlock(type='KnowledgeGraph', title='Relations', text='Plan relation.', source_segment_ids=['s1'],
                        nodes=[ReportGraphNode(id='a', label='Plan', source_segment_ids=['s1']),
                               ReportGraphNode(id='b', label='Alice', source_segment_ids=['s1'])],
                        edges=[ReportGraphEdge(source='a', target='b', label='review', relation='related',
                                                evidence_quote='Alice will review the plan.', source_segment_ids=['s1'])]),
        ]),
    ]
    return transcript, ReportRevision(
        report_id='verify', meeting_id='m', report_type='meeting', revision=1,
        document=ReportDocument(title='Verification report', executive_summary='Verified report.',
                                executive_summary_source_segment_ids=['s1'], sections=blocks, generation_method='llm'),
        created_at=now)


class CDP:
    def __init__(self, websocket):
        self.ws = websocket
        self.next_id = 0
        self.events = []

    async def call(self, method, params=None):
        self.next_id += 1
        request_id = self.next_id
        await self.ws.send(json.dumps({'id': request_id, 'method': method, 'params': params or {}}))
        while True:
            message = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=15))
            if message.get('id') == request_id:
                if 'error' in message:
                    raise AssertionError(f"CDP {method} failed: {message['error']}")
                return message.get('result', {})
            self.events.append(message)

    async def evaluate(self, expression):
        result = await self.call('Runtime.evaluate', {
            'expression': expression, 'returnByValue': True, 'awaitPromise': True,
        })
        if result.get('exceptionDetails'):
            raise AssertionError(result['exceptionDetails'])
        return result.get('result', {}).get('value')


@pytest.mark.skipif(chrome_binary() is None, reason='Google Chrome/Chromium is not installed')
def test_exported_html_runs_interactions_in_a_real_browser(tmp_path):
    import websockets

    transcript, revision = sample_report()
    report_path = tmp_path / 'report.html'
    write_standalone_report(report_path, revision, transcript)
    html = report_path.read_text(encoding='utf-8')
    assert 'http-equiv="Content-Security-Policy"' in html
    assert 'data-observation' in html and 'role="tablist"' in html

    async def exercise():
        profile = tmp_path / 'chrome-profile'
        profile.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen([
            chrome_binary(), '--headless=new', '--disable-gpu', '--no-first-run',
            '--no-default-browser-check', '--disable-background-networking',
            '--disable-component-update', '--disable-sync', '--disable-extensions',
            '--mute-audio', '--allow-file-access-from-files', '--remote-allow-origins=*',
            '--remote-debugging-port=0', f'--user-data-dir={profile}', 'about:blank',
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            port_file = profile / 'DevToolsActivePort'
            for _ in range(300):
                if port_file.exists() or process.poll() is not None:
                    break
                await asyncio.sleep(0.05)
            assert port_file.exists(), f'Chrome did not start its debugging port (exit={process.poll()})'
            port = int(port_file.read_text().splitlines()[0])
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=5) as response:
                targets = json.load(response)
            page = next(target for target in targets if target['type'] == 'page')
            async with websockets.connect(page['webSocketDebuggerUrl'], max_size=None) as websocket:
                cdp = CDP(websocket)
                await cdp.call('Page.enable')
                await cdp.call('Runtime.enable')
                await cdp.call('Log.enable')
                await cdp.call('Page.navigate', {'url': report_path.as_uri()})
                for _ in range(100):
                    if await cdp.evaluate('document.readyState') == 'complete':
                        break
                    await asyncio.sleep(0.05)
                await asyncio.sleep(0.15)

                assert await cdp.evaluate("document.querySelectorAll('#toc a').length") == 3
                assert await cdp.evaluate("document.querySelector('.items.timeline') !== null")
                assert await cdp.evaluate("document.querySelectorAll('.report-action input[type=checkbox]').length >= 1")
                assert await cdp.evaluate("document.querySelectorAll('.report-block--DataTable table, table').length >= 1")
                assert await cdp.evaluate("document.querySelector('.report-visual [data-observation]') !== null")

                assert await cdp.evaluate("""(() => {
                    const summary = [...document.querySelectorAll('details > summary')]
                        .find(item => item.textContent.includes('More context'));
                    const details = summary.closest('details');
                    summary.click();
                    return details.open === true;
                })()""")
                assert await cdp.evaluate("""(() => {
                    const group = document.querySelector('[data-layout-tabs]');
                    const buttons = group.querySelector('[role=tablist]').children;
                    buttons[1].click();
                    const panels = [...group.children].filter(item => item.matches('[role=tabpanel]'));
                    return buttons[1].getAttribute('aria-selected') === 'true' && panels[0].hidden && !panels[1].hidden;
                })()""")
                assert await cdp.evaluate("""(() => {
                    const box = document.querySelector('.report-action input[type=checkbox]');
                    box.click();
                    return box.checked === true;
                })()""")
                assert await cdp.evaluate("""(() => {
                    const mark = document.querySelector('.report-visual [data-observation="0"]');
                    mark.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                    return document.querySelector('[data-observation-panel="0"]').hidden === false;
                })()""")
                assert await cdp.evaluate("""(() => {
                    document.querySelector('#toc a').click();
                    return location.hash === '#part-1';
                })()""")
                resources = await cdp.evaluate(
                    "performance.getEntriesByType('resource').map(item => item.name).filter(name => !name.startsWith('file://'))")
                assert resources == []
                exceptions = [event for event in cdp.events if event.get('method') == 'Runtime.exceptionThrown']
                csp = [event for event in cdp.events if event.get('method') == 'Log.entryAdded'
                       and 'Content Security Policy' in json.dumps(event)]
                assert exceptions == []
                assert csp == []
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

    asyncio.run(exercise())
