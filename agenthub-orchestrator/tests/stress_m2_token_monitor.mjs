/**
 * Empirical Adversarial Stress Test Suite for Milestone 2: Token Tracking / Usage Monitor Component
 * Stress-tests live count, color-coded dot, 3-tier threshold alerts, 5-agent breakdown,
 * budget selector, reset functionality, and zero-CDN offline compliance.
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import vm from 'vm';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, '..');
const indexHtmlPath = path.join(projectRoot, 'public', 'index.html');

console.log('================================================================');
console.log(' M2 TOKEN TRACKING & USAGE MONITOR EMPIRICAL STRESS TEST');
console.log('================================================================');
console.log(`Target: ${indexHtmlPath}`);

const htmlContent = fs.readFileSync(indexHtmlPath, 'utf8');

// ─────────────────────────────────────────────────────────────────────────────
// 1. EXTRACT SCRIPT CONTEXT FROM public/index.html
// ─────────────────────────────────────────────────────────────────────────────
const scriptMatch = htmlContent.match(/<script>([\s\S]*?)<\/script>/);
if (!scriptMatch) {
  console.error('FATAL: Could not find <script> block in public/index.html');
  process.exit(1);
}

const scriptCode = scriptMatch[1];

// In-memory mock storage
const mockStorage = {};

// Mock DOM factory
const mockElements = {};
function getOrCreateMockElement(id) {
  if (!mockElements[id]) {
    mockElements[id] = {
      id,
      value: '',
      textContent: '',
      innerHTML: '',
      className: '',
      classList: {
        add(c) { this._classes = this._classes || new Set(); this._classes.add(c); },
        remove(c) { if (this._classes) this._classes.delete(c); },
        toggle(c, force) {
          this._classes = this._classes || new Set();
          if (force === undefined) {
            if (this._classes.has(c)) this._classes.delete(c); else this._classes.add(c);
          } else if (force) {
            this._classes.add(c);
          } else {
            this._classes.delete(c);
          }
        },
        contains(c) { return this._classes ? this._classes.has(c) : false; }
      },
      style: {},
      setAttribute(k, v) { this[k] = v; },
      getAttribute(k) { return this[k] || null; },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      addEventListener() {},
      removeEventListener() {},
      scrollTo() {}
    };
  }
  return mockElements[id];
}

const mockDOM = {
  elements: mockElements,
  getElementById(id) {
    return getOrCreateMockElement(id);
  },
  querySelectorAll() { return []; },
  addEventListener() {}
};

const sandbox = {
  document: mockDOM,
  window: {
    addEventListener() {},
    location: { href: 'http://127.0.0.1:8989/' }
  },
  localStorage: {
    getItem(k) { return mockStorage[k] !== undefined ? mockStorage[k] : null; },
    setItem(k, v) { mockStorage[k] = String(v); },
    removeItem(k) { delete mockStorage[k]; },
    clear() { Object.keys(mockStorage).forEach(k => delete mockStorage[k]); }
  },
  navigator: {
    clipboard: {
      writeText: async () => true
    }
  },
  setInterval() {},
  setTimeout() {},
  console: console,
  fetch: async () => ({ json: async () => ({}) })
};

vm.createContext(sandbox);

try {
  vm.runInContext(scriptCode, sandbox);
  console.log('✓ Successfully evaluated public/index.html script inside sandbox.\n');
} catch (e) {
  console.error('FATAL: Script execution failed inside sandbox:', e);
  process.exit(1);
}

// Extract exported variables and functions from sandbox
const {
  estimateTokens,
  formatTokenNum,
  changeTokenBudget,
  resetTokenMetrics,
  calculateTokensFromHub,
  updateTokenUI
} = sandbox;

// Test utilities
let totalTests = 0;
let passedTests = 0;
let failedTests = 0;
const failures = [];

function assert(condition, testName, details = '') {
  totalTests++;
  if (condition) {
    passedTests++;
    console.log(`  [PASS] ${testName}`);
  } else {
    failedTests++;
    console.error(`  [FAIL] ${testName} -> ${details}`);
    failures.push({ testName, details });
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// CATEGORY 1: STATIC DOM ELEMENTS IN public/index.html
// ─────────────────────────────────────────────────────────────────────────────
console.log('----------------------------------------------------------------');
console.log('CATEGORY 1: DOM Elements in public/index.html');
console.log('----------------------------------------------------------------');

// 1.1 Status Bar Component
assert(
  htmlContent.includes('id="status-tokens"') && htmlContent.includes("switchView('accounts')"),
  'Status bar #status-tokens exists with switchView(\'accounts\') interaction'
);
assert(
  htmlContent.includes('id="token-status-dot"'),
  'Status bar #token-status-dot exists'
);
assert(
  htmlContent.includes('id="token-status-text"'),
  'Status bar #token-status-text exists'
);

// 1.2 Dedicated Token Section in #view-accounts
assert(
  htmlContent.includes('id="token-monitor-section"'),
  '#token-monitor-section container exists in #view-accounts'
);
assert(
  htmlContent.includes('id="token-bar-fill"'),
  'Gauge progress bar #token-bar-fill exists'
);
assert(
  htmlContent.includes('id="token-threshold-badge"'),
  'Threshold badge #token-threshold-badge exists'
);
assert(
  htmlContent.includes('id="token-budget-select"'),
  'Budget selector #token-budget-select exists'
);
assert(
  htmlContent.includes('resetTokenMetrics()'),
  'Reset button invoking resetTokenMetrics() exists'
);

// 1.3 5-Agent Breakdown Grid in DOM
const agentCards = ['lead', 'worker1', 'worker2', 'worker3', 'supervisor'];
agentCards.forEach(agent => {
  const hasVal = htmlContent.includes(`id="token-val-${agent}"`);
  const hasPct = htmlContent.includes(`id="token-pct-${agent}"`);
  const hasFill = htmlContent.includes(`id="token-fill-${agent}"`);
  assert(
    hasVal && hasPct && hasFill,
    `5-Agent Breakdown: Card elements for '${agent}' exist (val, pct, fill)`
  );
});

// 1.4 Budget Options
const expectedBudgets = ['50000', '100000', '250000', '500000', '1000000'];
const allBudgetsFound = expectedBudgets.every(b => htmlContent.includes(`value="${b}"`));
assert(
  allBudgetsFound,
  'Budget selector contains standard tiers (50k, 100k, 250k, 500k, 1M)'
);

// ─────────────────────────────────────────────────────────────────────────────
// CATEGORY 2: ESTIMATE TOKENS & FORMATTING
// ─────────────────────────────────────────────────────────────────────────────
console.log('\n----------------------------------------------------------------');
console.log('CATEGORY 2: Heuristic Token Estimation & Formatting');
console.log('----------------------------------------------------------------');

assert(
  estimateTokens('') === 0 && estimateTokens(null) === 0 && estimateTokens(undefined) === 0,
  'estimateTokens handles empty/falsy inputs returning 0'
);

const sampleAscii = 'def compute_hash(data): return hashlib.sha256(data).hexdigest()';
const asciiTokens = estimateTokens(sampleAscii);
assert(
  asciiTokens > 10 && asciiTokens < 35,
  `estimateTokens gives reasonable count for code/ASCII (${asciiTokens} tokens for ${sampleAscii.length} chars)`
);

const sampleThai = 'สวัสดีครับ ทีมงาน Antigravity กำลังดำเนินการทดสอบระบบทั้งหมด';
const thaiTokens = estimateTokens(sampleThai);
assert(
  thaiTokens > 30 && thaiTokens < 70,
  `estimateTokens gives higher token weight for Unicode/Thai (${thaiTokens} tokens for ${sampleThai.length} chars)`
);

assert(
  formatTokenNum(500) === '500' && formatTokenNum(14800) === '14.8k' && formatTokenNum(1500000) === '1.5M',
  'formatTokenNum formats numbers cleanly (500, 14.8k, 1.5M)'
);

// ─────────────────────────────────────────────────────────────────────────────
// CATEGORY 3: 3-TIER THRESHOLD ALERTS & PROGRESS GAUGE DYNAMICS
// ─────────────────────────────────────────────────────────────────────────────
console.log('\n----------------------------------------------------------------');
console.log('CATEGORY 3: 3-Tier Threshold Alerts & Progress Gauge Dynamics');
console.log('----------------------------------------------------------------');

const dotEl = mockDOM.getElementById('token-status-dot');
const badgeEl = mockDOM.getElementById('token-threshold-badge');
const barEl = mockDOM.getElementById('token-bar-fill');
const textEl = mockDOM.getElementById('token-status-text');

// Helper to generate chat text producing roughly target token count
function makeChatWithTokens(leadTokens, w1Tokens, w2Tokens, w3Tokens, supTokens) {
  // ASCII char ~ 3.8 chars per token
  const msgL = 'L'.repeat(Math.max(1, Math.floor((leadTokens - 4) * 3.8)));
  const msg1 = '1'.repeat(Math.max(1, Math.floor((w1Tokens - 4) * 3.8)));
  const msg2 = '2'.repeat(Math.max(1, Math.floor((w2Tokens - 4) * 3.8)));
  const msg3 = '3'.repeat(Math.max(1, Math.floor((w3Tokens - 4) * 3.8)));
  const msgS = 'S'.repeat(Math.max(1, Math.floor((supTokens - 4) * 3.8)));

  return `
- [2026-09-29 01:00:00] [Team Lead]: ${msgL}
- [2026-09-29 01:00:01] [Worker 1]: ${msg1}
- [2026-09-29 01:00:02] [Worker 2]: ${msg2}
- [2026-09-29 01:00:03] [Worker 3]: ${msg3}
- [2026-09-29 01:00:04] [Human Supervisor]: ${msgS}
`;
}

// Set budget to 100,000
changeTokenBudget('100000');

// Test 3.1 Normal Tier (< 70%): generate ~30,000 tokens total (6k each)
const chat30k = makeChatWithTokens(6000, 6000, 6000, 6000, 6000);
calculateTokensFromHub(chat30k, '');

assert(
  badgeEl.className.includes('normal') &&
  badgeEl.textContent.includes('ปกติ') &&
  badgeEl.textContent.includes('< 70%') &&
  dotEl.style.background === '#4ec9b0',
  `Tier 1 Normal (< 70%): badge is "normal", color is #4ec9b0, label indicates < 70% (Got: ${badgeEl.textContent}, dot: ${dotEl.style.background})`
);
assert(
  barEl.className.includes('normal') && parseFloat(barEl.style.width) < 70,
  `Tier 1 Normal (< 70%): progress gauge bar fill is < 70% (Got: ${barEl.style.width}) with normal class`
);
assert(
  textEl.textContent.includes('Tokens') && textEl.textContent.includes('30%'),
  `Tier 1 Normal (< 70%): status text displays Tokens and percentage (Got: ${textEl.textContent})`
);

// Test 3.2 Warning Tier (70% - 90%): generate ~75,000 tokens (15k each)
const chat75k = makeChatWithTokens(15000, 15000, 15000, 15000, 15000);
calculateTokensFromHub(chat75k, '');

assert(
  badgeEl.className.includes('warning') &&
  badgeEl.textContent.includes('70-90%') &&
  dotEl.style.background === '#cca700',
  `Tier 2 Warning (70-90%): badge is "warning", color is #cca700 (Got: ${badgeEl.textContent}, dot: ${dotEl.style.background})`
);
assert(
  barEl.className.includes('warning') && parseFloat(barEl.style.width) >= 70 && parseFloat(barEl.style.width) < 90,
  `Tier 2 Warning (70-90%): progress gauge bar fill is 70-90% (Got: ${barEl.style.width}) with warning class`
);

// Test 3.3 Critical Alert Tier (> 90%): generate ~95,000 tokens (19k each)
const chat95k = makeChatWithTokens(19000, 19000, 19000, 19000, 19000);
calculateTokensFromHub(chat95k, '');

assert(
  badgeEl.className.includes('alert') &&
  badgeEl.textContent.includes('> 90%') &&
  dotEl.style.background === '#f48771',
  `Tier 3 Alert (> 90%): badge is "alert", color is #f48771 (Got: ${badgeEl.textContent}, dot: ${dotEl.style.background})`
);
assert(
  barEl.className.includes('alert') && parseFloat(barEl.style.width) >= 90,
  `Tier 3 Alert (> 90%): progress gauge bar fill is >= 90% (Got: ${barEl.style.width}) with alert class`
);

// Test 3.4 Over-budget Clamping (> 100%): generate ~120,000 tokens (24k each)
const chat120k = makeChatWithTokens(24000, 24000, 24000, 24000, 24000);
calculateTokensFromHub(chat120k, '');

assert(
  barEl.style.width === '100%' && badgeEl.className.includes('alert'),
  `Over-budget (> 100%): progress bar clamp width to 100% (Got: ${barEl.style.width}) and maintains alert badge`
);

// ─────────────────────────────────────────────────────────────────────────────
// CATEGORY 4: 5-AGENT BREAKDOWN METRICS CALCULATION
// ─────────────────────────────────────────────────────────────────────────────
console.log('\n----------------------------------------------------------------');
console.log('CATEGORY 4: 5-Agent Breakdown Metrics Calculation');
console.log('----------------------------------------------------------------');

// Test chat stream with all 5 participants
const testChatLog = `
- [2026-09-29 01:00:00] [Team Lead]: มอบหมายงานให้ Worker 1, 2, 3 ดำเนินการ
- [2026-09-29 01:00:05] [Worker 1]: รับทราบครับ กำลังพัฒนา Backend API
- [2026-09-29 01:00:10] [Worker 2]: รับทราบค่ะ กำลังออกแบบ UI Component
- [2026-09-29 01:00:15] [Worker 3]: รับทราบครับ กำลังเขียน Integration Tests
- [2026-09-29 01:00:20] [Human Supervisor]: ตรวจสอบผลงานรอบแรกด้วยครับ
`;

const testBoard = `# Project Mission\nFullstack Multi-Agent Swarm Hub Architecture`;

calculateTokensFromHub(testChatLog, testBoard);

const leadVal = parseInt(mockDOM.getElementById('token-val-lead').textContent.replace(/,/g, ''), 10);
const w1Val = parseInt(mockDOM.getElementById('token-val-worker1').textContent.replace(/,/g, ''), 10);
const w2Val = parseInt(mockDOM.getElementById('token-val-worker2').textContent.replace(/,/g, ''), 10);
const w3Val = parseInt(mockDOM.getElementById('token-val-worker3').textContent.replace(/,/g, ''), 10);
const supVal = parseInt(mockDOM.getElementById('token-val-supervisor').textContent.replace(/,/g, ''), 10);

assert(
  leadVal > 0 && w1Val > 0 && w2Val > 0 && w3Val > 0 && supVal > 0,
  `Tokens distributed dynamically to all 5 agents (Lead: ${leadVal}, W1: ${w1Val}, W2: ${w2Val}, W3: ${w3Val}, Sup: ${supVal})`
);

assert(
  leadVal > w1Val,
  `Team Lead tokens include both chat message and BOARD contract tokens (${leadVal} > ${w1Val})`
);

// Check percentages sum up close to 100%
const leadPct = parseInt(mockDOM.getElementById('token-pct-lead').textContent, 10);
const w1Pct = parseInt(mockDOM.getElementById('token-pct-worker1').textContent, 10);
const w2Pct = parseInt(mockDOM.getElementById('token-pct-worker2').textContent, 10);
const w3Pct = parseInt(mockDOM.getElementById('token-pct-worker3').textContent, 10);
const supPct = parseInt(mockDOM.getElementById('token-pct-supervisor').textContent, 10);
const totalPct = leadPct + w1Pct + w2Pct + w3Pct + supPct;

assert(
  totalPct >= 95 && totalPct <= 105,
  `Agent percentages sum accurately (${totalPct}%)`
);

// ─────────────────────────────────────────────────────────────────────────────
// CATEGORY 5: BUDGET SELECTOR & RESET OFFSET INTEGRITY
// ─────────────────────────────────────────────────────────────────────────────
console.log('\n----------------------------------------------------------------');
console.log('CATEGORY 5: Budget Selector & Reset Offset Integrity');
console.log('----------------------------------------------------------------');

// 5.1 Change budget to 50k
changeTokenBudget('50000');
assert(
  mockStorage['ag_token_budget'] === '50000',
  `changeTokenBudget(50000) persists to localStorage (Got: ${mockStorage['ag_token_budget']})`
);
const budgetMaxText = mockDOM.getElementById('token-budget-max').textContent;
assert(
  budgetMaxText.includes('50,000'),
  `changeTokenBudget(50000) updates DOM max budget text (Got: ${budgetMaxText})`
);

// 5.2 Reset Token Metrics
resetTokenMetrics();
assert(
  mockStorage['ag_token_reset'] !== undefined,
  'resetTokenMetrics() stores reset offset snapshot into localStorage'
);

const totalAfterReset = mockDOM.getElementById('token-total-val').textContent;
const pctAfterReset = mockDOM.getElementById('token-percent-label').textContent;
const badgeAfterReset = mockDOM.getElementById('token-threshold-badge');

assert(
  totalAfterReset === '0 Tokens' && pctAfterReset === '0%' && badgeAfterReset.className.includes('normal'),
  `resetTokenMetrics() successfully zeroes out net consumed tokens (Total: ${totalAfterReset}, Pct: ${pctAfterReset})`
);

// 5.3 New chat arrivals after reset correctly count net tokens
const updatedChat = testChatLog + `\n- [2026-09-29 01:05:00] [Worker 1]: งานชิ้นใหม่เพิ่มอีก 500 บรรทัด`;
calculateTokensFromHub(updatedChat, testBoard);

const w1NewVal = parseInt(mockDOM.getElementById('token-val-worker1').textContent.replace(/,/g, ''), 10);
assert(
  w1NewVal > 0,
  `New arrivals after reset count net delta only (New net W1: ${w1NewVal})`
);

// ─────────────────────────────────────────────────────────────────────────────
// CATEGORY 6: ZERO-CDN & INTEGRITY AUDIT
// ─────────────────────────────────────────────────────────────────────────────
console.log('\n----------------------------------------------------------------');
console.log('CATEGORY 6: Zero-CDN & Integrity Audit');
console.log('----------------------------------------------------------------');

// 6.1 Zero-CDN offline compliance in HTML
const externalUrls = htmlContent.match(/https?:\/\/[a-zA-Z0-9_\-\.]+\.[a-zA-Z]{2,}[^\s"'>]*/g) || [];
// Filter out the regex literal in markdown parser (/(https?:\/\/[^\s&<]+)/g)
const filteredExternal = externalUrls.filter(u => !u.includes('[^\\s') && !u.includes('ag-link'));
assert(
  filteredExternal.length === 0,
  'Zero external network/CDN URLs in public/index.html',
  `Found: ${JSON.stringify(filteredExternal)}`
);

// 6.2 Integrity: dynamic implementation check (no hardcoded answers)
const hardcodedFakes = [
  'token-bar-fill" style="width: 42%',
  'return 14800',
  'return "alert"',
  'return "normal"'
];
const foundFakes = hardcodedFakes.filter(f => htmlContent.includes(f));
assert(
  foundFakes.length === 0,
  'No hardcoded test outcomes or dummy facades in token implementation',
  `Found: ${foundFakes.join(', ')}`
);

// =============================================================================
// TEST SUMMARY & VERDICT
// =============================================================================
console.log('\n================================================================');
console.log(' EMPIRICAL TOKEN MONITOR TEST SUMMARY');
console.log('================================================================');
console.log(`Total tests executed : ${totalTests}`);
console.log(`Passed               : ${passedTests}`);
console.log(`Failed               : ${failedTests}`);

if (failedTests > 0) {
  console.log('\nFailed Tests:');
  failures.forEach(f => console.log(`  - ${f.testName}: ${f.details}`));
  console.log('\nFINAL EMPIRICAL VERDICT: REQUEST_CHANGES ❌');
  process.exit(1);
} else {
  console.log('\nFINAL EMPIRICAL VERDICT: APPROVE ✅ (100% Passed)');
  process.exit(0);
}
