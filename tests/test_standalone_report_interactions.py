import subprocess
from datetime import datetime, timezone
from html.parser import HTMLParser

from meetingbro.reports.standalone import _SCRIPT, write_standalone_report
from meetingbro.schemas import ReportBlock, ReportDocument, ReportRevision, ReportSection


class Templates(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}
        self.current = None
    def handle_starttag(self, tag, attrs):
        if tag == 'template':
            self.current = dict(attrs)['id']
            self.values[self.current] = ''
    def handle_data(self, data):
        if self.current:
            self.values[self.current] += data
    def handle_endtag(self, tag):
        if tag == 'template': self.current = None


def test_copy_templates_include_hidden_sections_and_escape_input(tmp_path):
    document = ReportDocument(title='Report', executive_summary='Summary', sections=[
        ReportSection(title='First', blocks=[ReportBlock(text='First detail')]),
        ReportSection(title='Second', blocks=[ReportBlock(text='</template><script>alert(1)</script>')]),
    ])
    report = ReportRevision(report_id='r', meeting_id='m', report_type='meeting', revision=1,
                            document=document, created_at=datetime.now(timezone.utc))
    path = tmp_path / 'report.html'
    write_standalone_report(path, report, [])
    html = path.read_text()
    assert html.count('<script>') == 1
    parser = Templates()
    parser.feed(html)
    assert 'First detail' in parser.values['copy-full']
    assert '</template><script>alert(1)</script>' in parser.values['copy-full']
    assert parser.values['copy-2'].startswith('Second')


def test_presentation_and_copy_logic_without_a_browser(tmp_path):
    harness = r'''
const assert = require('node:assert/strict');
class Element {
  constructor() { this.hidden = false; this.disabled = false; this.textContent = ''; this.listeners = {}; this.style = {}; }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  click() { this.listeners.click?.(); }
  setAttribute() {} select() {} remove() {}
}
const sections = [new Element(), new Element()];
const elements = Object.fromEntries(['mode','pager','prev','next','counter','toc','fullscreen','copy-status'].map(k => [k,new Element()]));
elements['copy-full'] = {content:{textContent:'Title\nFirst section\nSecond section'}};
const copyButton = new Element(); copyButton.dataset = {copy:'copy-full'};
let keyHandler, clipboard = '';
global.document = {
  querySelectorAll: q => q === '[data-section]' ? sections : [copyButton],
  getElementById: id => elements[id],
  addEventListener: (event, fn) => { if (event === 'keydown') keyHandler = fn; },
  documentElement: {}, createElement: () => new Element(), body: {appendChild() {}}, execCommand: () => true,
};
Object.defineProperty(global, 'navigator', {value:{clipboard:{writeText: async text => { clipboard = text; }}}, configurable:true});
'''
    checks = r'''
(async () => {
  elements.mode.click();
  assert.equal(sections[0].hidden, false); assert.equal(sections[1].hidden, true);
  elements.next.click();
  assert.equal(elements.counter.textContent, '2 / 2'); assert.equal(sections[0].hidden, true);
  copyButton.click(); await new Promise(resolve => setImmediate(resolve));
  assert.match(clipboard, /First section/); assert.match(clipboard, /Second section/);
  assert.equal(elements['copy-status'].textContent, 'Copied / 已複製');
  keyHandler({key:'ArrowLeft',target:{tagName:'BODY'}});
  assert.equal(elements.counter.textContent, '1 / 2');
  Object.defineProperty(global, 'navigator', {value:{}, configurable:true});
  copyButton.click(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(elements['copy-status'].textContent, 'Copied / 已複製');
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    path = tmp_path / 'test.cjs'
    path.write_text(harness + _SCRIPT + checks)
    subprocess.run(['node', str(path)], check=True)
