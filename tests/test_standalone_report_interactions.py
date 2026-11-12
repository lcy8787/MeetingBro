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
    document = ReportDocument(generation_method='llm', title='Report', executive_summary='Summary', sections=[
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


def test_option_switch_and_copy_logic_without_a_browser(tmp_path):
    harness = r'''
const assert = require('node:assert/strict');
class Element {
  constructor() { this.hidden = false; this.textContent = ''; this.listeners = {}; this.style = {}; this.attrs = {}; }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  click() { this.listeners.click?.(); }
  setAttribute(key, value) { this.attrs[key] = value; } select() {} remove() {}
}
const buttons = [new Element(), new Element()], panels = [new Element(), new Element()];
const group = {querySelectorAll: q => q === 'button' ? buttons : panels};
const status = new Element();
const template = {content:{textContent:'Full report including hidden explanations'}};
const copyButton = new Element(); copyButton.dataset = {copy:'copy-full'};
let clipboard = '';
global.document = {
  querySelectorAll: q => q === '[data-copy]' ? [copyButton] : q === '[data-choices]' ? [group] : [],
  getElementById: id => id === 'copy-status' ? status : template,
  createElement: () => new Element(), body: {appendChild() {}}, execCommand: () => true,
};
Object.defineProperty(global, 'navigator', {value:{clipboard:{writeText: async text => { clipboard = text; }}}, configurable:true});
'''
    checks = r'''
(async () => {
  buttons[1].click();
  assert.equal(buttons[1].attrs['aria-pressed'], 'true');
  assert.equal(buttons[0].attrs['aria-pressed'], 'false');
  assert.equal(panels[0].hidden, true); assert.equal(panels[1].hidden, false);
  copyButton.click(); await new Promise(resolve => setImmediate(resolve));
  assert.match(clipboard, /hidden explanations/);
  assert.equal(status.textContent, 'Copied / 已複製');
  Object.defineProperty(global, 'navigator', {value:{}, configurable:true});
  copyButton.click(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(status.textContent, 'Copied / 已複製');
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    path = tmp_path / 'test.cjs'
    path.write_text(harness + _SCRIPT + checks)
    subprocess.run(['node', str(path)], check=True)


def test_standalone_series_filter_uses_attributes_for_svg_marks(tmp_path):
    # SVGElement has no HTMLElement.hidden setter: assigning .hidden does not hide a mark.
    harness = r'''
const assert = require('node:assert/strict');
class SvgMark {
  constructor(series) { this.dataset = {series}; this.attributes = new Map(); }
  toggleAttribute(name, enabled) { if (enabled) this.attributes.set(name, ''); else this.attributes.delete(name); }
}
class Button {
  constructor(series) { this.dataset = {seriesSelect:series}; this.listeners = {}; this.attributes = {}; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  setAttribute(name, value) { this.attributes[name] = value; }
  click() { this.listeners.click(); }
}
const all = new Button('-1'), first = new Button('0'), second = new Button('1');
const buttons = [all, first, second], marks = [new SvgMark('0'), new SvgMark('1')];
const group = {querySelectorAll: q => q === '[data-series-select]' ? buttons : q === '[data-series]' ? marks : []};
global.document = {getElementById: () => ({}), querySelectorAll: q => q === '[data-visual]' ? [group] : []};
'''
    checks = r'''
second.click();
assert.equal(marks[0].attributes.has('hidden'), true);
assert.equal(marks[1].attributes.has('hidden'), false);
assert.equal(second.attributes['aria-pressed'], 'true');
assert.equal(first.attributes['aria-pressed'], 'false');
all.click();
assert.equal(marks[0].attributes.has('hidden'), false);
assert.equal(marks[1].attributes.has('hidden'), false);
'''
    path = tmp_path / 'svg-series.cjs'
    path.write_text(harness + _SCRIPT + checks)
    subprocess.run(['node',str(path)],check=True)
