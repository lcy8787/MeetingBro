import subprocess
from pathlib import Path


def test_desktop_defaults_and_independent_switches():
    repo = Path(__file__).resolve().parents[1]
    script = r'''
const assert = require('node:assert/strict');
const Module = require('node:module');
const originalLoad = Module._load;
Module._load = function(name, ...rest) { return name === 'electron' ? {app:{}} : originalLoad.call(this, name, ...rest); };
const store = require('./app/frontend/electron/settings-store.cjs');
const initial = store.defaultSettings();
assert.equal(initial.qwenModelSize, '0.6B');
assert.equal(initial.speechModel, 'qwen3');
assert.equal(store.settingsToEnv({...initial,speechModel:'cantonese-hk-gguf'}).MEETINGBRO_ASR_MODEL, 'cantonese-hk-gguf');
assert.notEqual(store.startupSignature(initial), store.startupSignature({...initial,speechModel:'cantonese-hk-gguf'}));
assert.equal(initial.liveSummariesEnabled, false);
assert.equal(initial.finalSummaryEnabled, false);
assert.equal(initial.whisperEngine, undefined);
const migrated = store.settingsToEnv({...initial, qwenModelSize:'1.7B', whisperEngine:'faster-whisper', whisperSize:'large-v3'});
assert.equal(migrated.MEETINGBRO_QWEN3_MODEL_SIZE, '1.7B');
assert.equal(Object.keys(migrated).some(key => key.includes('WHISPER')), false);
assert.notEqual(store.startupSignature(initial), store.startupSignature({...initial,qwenModelSize:'1.7B'}));
assert.equal(initial.llm.baseUrl, 'http://127.0.0.1:11434/v1');
const env = store.settingsToEnv({...initial, liveSummariesEnabled:false, finalSummaryEnabled:true});
assert.equal(env.MEETINGBRO_LIVE_SUMMARIES_ENABLED, 'false');
assert.equal(env.MEETINGBRO_FINAL_SUMMARY_ENABLED, 'true');
assert.equal(env.MEETINGBRO_LLM_RESOURCE_MODE, 'balanced');
assert.equal(store.settingsToEnv({...initial, llmResourceMode:'post_recording'}).MEETINGBRO_LLM_RESOURCE_MODE, 'post_recording');
assert.equal(store.settingsToEnv({...initial, llmResourceMode:'invalid'}).MEETINGBRO_LLM_RESOURCE_MODE, 'balanced');
assert.notEqual(store.startupSignature(initial), store.startupSignature({...initial, finalSummaryEnabled:true}));
'''
    subprocess.run(['node', '-e', script], cwd=repo, check=True)
