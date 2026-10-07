/**
 * Empirical Adversarial Stress Test Suite for Milestone 2: Frontend UI/UX
 * Directly extracts and stress-tests client-side logic from public/index.html.
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
console.log(' M2 FRONTEND EMPIRICAL ADVERSARIAL STRESS TEST HARNESS');
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

// Set up mock browser environment
const mockDOM = {
  elements: {},
  getElementById(id) {
    if (!this.elements[id]) {
      this.elements[id] = {
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
    return this.elements[id];
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

const {
  escapeHtml,
  highlightCode,
  renderMarkdown,
  resolveSenderMeta,
  parseChatLog,
  finalizeMessage
} = sandbox;

// ─────────────────────────────────────────────────────────────────────────────
// TEST RUNNER UTILITIES
// ─────────────────────────────────────────────────────────────────────────────
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
// CATEGORY 1: OFFLINE RESILIENCE & ZERO-CDN VERIFICATION
// ─────────────────────────────────────────────────────────────────────────────
console.log('----------------------------------------------------------------');
console.log('CATEGORY 1: Offline Resilience & Zero-CDN Verification');
console.log('----------------------------------------------------------------');

// 1.1 Zero external network references in HTML attributes (src/href/url)
const externalSrcMatches = htmlContent.match(/(src|href|url)=["']?(https?:|\/\/)[^"'\s>]+/gi) || [];
assert(
  externalSrcMatches.length === 0,
  'Zero external network references in HTML attributes (src/href/url)',
  `Found: ${JSON.stringify(externalSrcMatches)}`
);

// 1.2 Zero CDN domain references
const cdnKeywords = ['unpkg.com', 'cdnjs.cloudflare.com', 'cdn.jsdelivr.net', 'googleapis.com', 'gstatic.com', 'bootstrapcdn.com', 'fontawesome'];
const foundCdns = cdnKeywords.filter(cdn => htmlContent.toLowerCase().includes(cdn));
assert(
  foundCdns.length === 0,
  'Zero CDN references (unpkg, cdnjs, google fonts, etc.)',
  `Found CDN keywords: ${foundCdns.join(', ')}`
);

// 1.3 Local fonts / system font fallback
const hasFontFallback = htmlContent.includes('-apple-system') || htmlContent.includes('Segoe UI') || htmlContent.includes('sans-serif');
assert(
  hasFontFallback,
  'Typography uses system-native and offline font stacks',
  'Missing standard system font fallbacks'
);

// 1.4 API calls use relative paths
const apiCalls = [...scriptCode.matchAll(/fetch\s*\(\s*['"`]([^'"`]+)['"`]/g)].map(m => m[1]);
const nonRelativeApiCalls = apiCalls.filter(url => url.startsWith('http://') || url.startsWith('https://'));
assert(
  nonRelativeApiCalls.length === 0,
  'All client API requests use relative local paths (e.g., /api/...)',
  `Found absolute API URLs: ${JSON.stringify(nonRelativeApiCalls)}`
);


// ─────────────────────────────────────────────────────────────────────────────
// CATEGORY 2: MALFORMED & ADVERSARIAL MARKDOWN TABLES
// ─────────────────────────────────────────────────────────────────────────────
console.log('\n----------------------------------------------------------------');
console.log('CATEGORY 2: Malformed & Adversarial Markdown Tables');
console.log('----------------------------------------------------------------');

// 2.1 Standard valid table
const tableStandard = `| Col A | Col B | Col C |
|:---|:---:|---:|
| Val 1 | Val 2 | Val 3 |
| Val 4 | Val 5 | Val 6 |
`;
const outStandard = renderMarkdown(tableStandard);
assert(
  outStandard.includes('<table class="ag-table">') &&
  outStandard.includes('<th>Col A</th>') &&
  outStandard.includes('<td>Val 1</td>'),
  'Standard markdown table renders with thead/tbody'
);

// 2.2 Table missing separator line (should not crash, fallback gracefully)
const tableNoSep = `| Col A | Col B |
| Val 1 | Val 2 |
`;
let outNoSep = '';
let noSepThrew = false;
try {
  outNoSep = renderMarkdown(tableNoSep);
} catch (e) {
  noSepThrew = true;
}
assert(
  !noSepThrew && !outNoSep.includes('NaN') && !outNoSep.includes('undefined'),
  'Table missing separator row does not crash or generate undefined/NaN'
);

// 2.3 Table with mismatched column counts
const tableMismatched = `| Col A | Col B |
|---|---|
| Val 1 | Val 2 | Val 3 | Val 4 |
| Val Single |
`;
let outMismatched = '';
let mismatchThrew = false;
try {
  outMismatched = renderMarkdown(tableMismatched);
} catch (e) {
  mismatchThrew = true;
}
assert(
  !mismatchThrew && outMismatched.includes('<table') && outMismatched.includes('Val 3'),
  'Table with mismatched cell counts renders safely without throw'
);

// 2.4 Table with empty cells and excessive pipes
const tableEmptyPipes = `||||
|---|---|---|
||||
|   |   |   |
`;
let outEmptyPipes = '';
let emptyPipesThrew = false;
try {
  outEmptyPipes = renderMarkdown(tableEmptyPipes);
} catch (e) {
  emptyPipesThrew = true;
}
assert(
  !emptyPipesThrew && outEmptyPipes.includes('<table'),
  'Table with empty cells and pipe-only lines renders safely'
);

// 2.5 Table with XSS payloads inside table cells
const tableXss = `| Script Header | Img Header |
|---|---|
| <script>alert("xss")</script> | <img src=x onerror=alert(1)> |
`;
const outTableXss = renderMarkdown(tableXss);
assert(
  !outTableXss.includes('<script>') &&
  outTableXss.includes('&lt;script&gt;') &&
  outTableXss.includes('&lt;img'),
  'Table cells properly escape HTML entities (<script>, <img>)'
);

// 2.6 Table without trailing newline (CHALLENGE / BUG REPRODUCTION)
const tableNoTrailingNl = "| Col 1 | Col 2 |\n|---|---|\n| Row 1 | Row 2 |";
const outNoTrailingNl = renderMarkdown(tableNoTrailingNl);
assert(
  outNoTrailingNl.includes('<td>Row 1</td>'),
  'Table without trailing newline renders all rows inside <tbody>',
  `BUG REPRODUCED: Last row was excluded from table! Output: ${outNoTrailingNl.replace(/\n/g, ' ')}`
);


// ─────────────────────────────────────────────────────────────────────────────
// CATEGORY 3: CODE BLOCK PARSER & SYNTAX HIGHLIGHTING RESILIENCE
// ─────────────────────────────────────────────────────────────────────────────
console.log('\n----------------------------------------------------------------');
console.log('CATEGORY 3: Code Block Parser & Syntax Highlighting Resilience');
console.log('----------------------------------------------------------------');

// 3.1 Unclosed code fence (should not crash, should escape content)
const unclosedCode = '```python\ndef calculate(a, b):\n    return a + b\n';
let outUnclosed = '';
let unclosedThrew = false;
try {
  outUnclosed = renderMarkdown(unclosedCode);
} catch (e) {
  unclosedThrew = true;
}
assert(
  !unclosedThrew && !outUnclosed.includes('undefined'),
  'Unclosed code fence does not crash parser'
);

// 3.2 Code block containing replacement dollar tokens ($&, $', $`, $1) (CHALLENGE / BUG REPRODUCTION)
const codeWithAmp = "```bash\necho $&\n```";
const outAmp = renderMarkdown(codeWithAmp);
assert(
  !outAmp.includes('%%CODE_BLOCK_'),
  'Code block containing $& does not inject internal placeholder tokens (%%CODE_BLOCK_0%%)',
  `BUG REPRODUCED: Internal placeholder leaked into code block output: ${outAmp.replace(/\n/g, ' ')}`
);

// 3.3 Single quote handling in highlightCode (CHALLENGE / BUG REPRODUCTION)
const singleQuoteCode = "echo 'hello world'";
const outSingleQuote = highlightCode(singleQuoteCode, 'bash');
assert(
  !outSingleQuote.includes('<span <span') && !outSingleQuote.includes('&<span'),
  'highlightCode on single quotes does not corrupt HTML tags or entity references',
  `BUG REPRODUCED: Malformed HTML tag created: ${outSingleQuote}`
);

// 3.4 String highlighting functionality (CHALLENGE / BUG REPRODUCTION)
const testStringCode = 'msg = "hello world"';
const outTestString = highlightCode(testStringCode, 'python');
assert(
  outTestString.includes('class="tok-string"'),
  'String literal in code block receives tok-string syntax highlighting',
  `BUG REPRODUCED: Strings are never highlighted because escapeHtml ran before string regex: ${outTestString}`
);

// 3.5 Tag attribute replacement recursion (class inside class="tok-comment") (CHALLENGE / BUG REPRODUCTION)
const commentCode = "# comment";
const outComment = highlightCode(commentCode, 'python');
assert(
  !outComment.includes('<span <span class="tok-keyword">class</span>='),
  'highlightCode does not replace attribute names inside its own generated span tags',
  `BUG REPRODUCED: Tag attribute corrupted into: ${outComment}`
);

// 3.6 Code block with HTML tags inside
const codeWithHtml = "```html\n<div class=\"test\">Hello & Welcome</div>\n```";
const outCodeHtml = renderMarkdown(codeWithHtml);
assert(
  !outCodeHtml.includes('<div class="test">') && outCodeHtml.includes('&lt;div'),
  'Code block containing raw HTML tags properly escapes entities'
);

// 3.7 Empty code fence
const emptyCode = "```\n\n```";
const outEmptyCode = renderMarkdown(emptyCode);
assert(
  outEmptyCode.includes('<div class="code-block">'),
  'Empty code block renders clean container'
);


// ─────────────────────────────────────────────────────────────────────────────
// CATEGORY 4: SPECIAL CHARACTERS, UNICODE, INJECTION & SANITIZATION
// ─────────────────────────────────────────────────────────────────────────────
console.log('\n----------------------------------------------------------------');
console.log('CATEGORY 4: Special Characters, Unicode & Sanitization');
console.log('----------------------------------------------------------------');

// 4.1 Script tag and SVG event handler injection
const xssPayloads = [
  '<script>alert(1)</script>',
  '<svg/onload=alert(document.cookie)>',
  '<iframe src="javascript:alert(1)"></iframe>',
  '<a href="javascript:alert(1)">Click Me</a>'
];
let allXssSanitized = true;
let xssLeakDetail = '';
xssPayloads.forEach(payload => {
  const rendered = renderMarkdown(payload);
  if (rendered.includes('<script>') || (rendered.includes('<svg') && rendered.includes('onload=')) || rendered.includes('<iframe') || rendered.includes('href="javascript:')) {
    allXssSanitized = false;
    xssLeakDetail = rendered;
  }
});
assert(
  allXssSanitized,
  'All raw XSS injection vectors (<script>, <svg onload>, <iframe>, javascript:) are neutralized',
  xssLeakDetail
);

// 4.2 Thai characters and complex emojis
const thaiEmoji = 'สวัสดีครับ Swarm 🚀 👑 🛠️ 🧪 🎨 📢 ข้อความทดสอบระบบสระภาษาไทย: ที่นี่มีข้อความซับซ้อน';
const outThai = renderMarkdown(thaiEmoji);
assert(
  outThai.includes('สวัสดีครับ') && outThai.includes('🚀') && outThai.includes('ที่นี่มีข้อความซับซ้อน'),
  'Thai script and unicode emojis preserved accurately'
);

// 4.3 Ampersands and quote escaping in inline code
const inlineSpecial = 'Run `curl -X POST -H "Content-Type: application/json" & test`';
const outInline = renderMarkdown(inlineSpecial);
assert(
  outInline.includes('&amp;') && outInline.includes('&quot;'),
  'Inline code with ampersands and quotes properly HTML-escaped'
);

// 4.4 URL auto-linking
const urlText = 'Check docs at https://antigravity.google.com/docs and http://localhost:8989';
const outUrl = renderMarkdown(urlText);
assert(
  outUrl.includes('<a href="https://antigravity.google.com/docs"') && outUrl.includes('target="_blank"'),
  'Auto-link replaces standard HTTP/HTTPS URLs with target="_blank"'
);


// ─────────────────────────────────────────────────────────────────────────────
// CATEGORY 5: MULTI-LINE BLOCKS & LAYOUT INTEGRITY
// ─────────────────────────────────────────────────────────────────────────────
console.log('\n----------------------------------------------------------------');
console.log('CATEGORY 5: Multi-Line Blocks & Structural Integrity');
console.log('----------------------------------------------------------------');

// 5.1 Multi-line bullet lists wrapped in <ul>
const listMarkdown = `- Item 1\n- Item 2\n- Item 3\n* Item 4`;
const outList = renderMarkdown(listMarkdown);
assert(
  outList.includes('<ul class="ag-ul">') &&
  outList.includes('<li class="ag-li">Item 1</li>') &&
  outList.includes('<li class="ag-li">Item 4</li>'),
  'Multi-line bullet list wrapped in <ul class="ag-ul">'
);

// 5.2 Multi-line blockquotes
const quoteMarkdown = `> Header quote\n> Detail line 1\n> Detail line 2`;
const outQuote = renderMarkdown(quoteMarkdown);
assert(
  outQuote.includes('<blockquote class="ag-quote">'),
  'Blockquotes render with ag-quote class'
);

// 5.3 Headers h1, h2, h3
const headersMarkdown = `# H1 Title\n## H2 Subtitle\n### H3 Section`;
const outHeaders = renderMarkdown(headersMarkdown);
assert(
  outHeaders.includes('<h2 class="ag-h2">H1 Title</h2>') &&
  outHeaders.includes('<h3 class="ag-h3">H2 Subtitle</h3>') &&
  outHeaders.includes('<h4 class="ag-h4">H3 Section</h4>'),
  'Headers H1-H3 map cleanly to designated styled headings'
);

// 5.4 Horizontal rules
const hrMarkdown = `Before HR\n---\nAfter HR`;
const outHr = renderMarkdown(hrMarkdown);
assert(
  outHr.includes('<hr class="ag-hr" />'),
  'Horizontal rule (---) renders <hr class="ag-hr" />'
);

// 5.5 Very large payload (100,000 characters) - Performance check
const largePayload = ('Line of text with `inline code` and **bold**.\n\n').repeat(2000);
const startPerf = Date.now();
const outLarge = renderMarkdown(largePayload);
const durationMs = Date.now() - startPerf;
assert(
  durationMs < 500 && outLarge.length > 50000,
  `Performance check: 100k character markdown parsed in ${durationMs}ms (<500ms)`,
  `Took ${durationMs}ms`
);


// ─────────────────────────────────────────────────────────────────────────────
// CATEGORY 6: CHAT LOG PARSER (parseChatLog) & DELIMITER EDGE CASES
// ─────────────────────────────────────────────────────────────────────────────
console.log('\n----------------------------------------------------------------');
console.log('CATEGORY 6: Chat Log Parser & Delimiter Resilience');
console.log('----------------------------------------------------------------');

// 6.1 Empty / Null / Undefined chat logs
assert(parseChatLog(null).length === 0, 'parseChatLog(null) returns empty array');
assert(parseChatLog(undefined).length === 0, 'parseChatLog(undefined) returns empty array');
assert(parseChatLog('').length === 0, 'parseChatLog("") returns empty array');
assert(parseChatLog('   \n\n   ').length === 0, 'parseChatLog whitespace-only returns empty array');

// 6.2 Standard multi-agent conversation
const chatLogSample = `
# Swarm Team Live Chat
- [2026-09-28 17:00:00] [System]: Swarm workspace initialized.
- [2026-09-28 17:01:00] [Team Lead]: Team, here is the plan:
| Worker | Responsibility |
|---|---|
| Worker 1 | API Core |
| Worker 2 | Frontend |
- [2026-09-28 17:02:00] [Worker 1]: Got it. Implementing now:
\`\`\`python
def hello():
    return "OK"
\`\`\`
- [2026-09-28 17:03:00] [Worker 2]: Working on UI templates.
- [2026-09-28 17:04:00] [Human Supervisor]: Proceed with testing.
`;

const parsedMessages = parseChatLog(chatLogSample);
assert(
  parsedMessages.length === 5,
  `parseChatLog parsed 5 distinct messages (got ${parsedMessages.length})`
);

// 6.3 Verify role resolution and badges for each agent
const roleCheckMap = {
  'System': 'SYSTEM',
  'Team Lead': 'LEAD',
  'Worker 1': 'WORKER 1',
  'Worker 2': 'WORKER 2',
  'Human Supervisor': 'SUPERVISOR'
};
let allRolesMatch = true;
parsedMessages.forEach(msg => {
  const expectedRole = roleCheckMap[msg.sender];
  if (expectedRole && msg.meta.role !== expectedRole) {
    allRolesMatch = false;
    console.error(`  Role mismatch for ${msg.sender}: expected ${expectedRole}, got ${msg.meta.role}`);
  }
});
assert(allRolesMatch, 'All agent roles correctly mapped to color/badge configurations');

// 6.4 Multi-line code block in chat message parsed into renderedHtml
const w1Msg = parsedMessages.find(m => m.sender === 'Worker 1');
assert(
  w1Msg && w1Msg.renderedHtml.includes('class="code-block"'),
  'Fenced code block inside chat message renders into code-block DOM'
);

// 6.5 Table inside chat message parsed into renderedHtml
const leadMsg = parsedMessages.find(m => m.sender === 'Team Lead');
assert(
  leadMsg && leadMsg.renderedHtml.includes('<table class="ag-table">'),
  'Markdown table inside chat message renders into ag-table DOM'
);

// 6.6 Delimiter with backticks formatting: - `[2026-09-28 17:05:00] [Worker 3]` Status update
const chatBackticks = "- `[2026-09-28 17:05:00] [Worker 3]` Status update";
const parsedBackticks = parseChatLog(chatBackticks);
assert(
  parsedBackticks.length === 1 && parsedBackticks[0].sender === 'Worker 3',
  'Chat delimiter with backticks (`[...] [...]`) parsed successfully'
);

// 6.7 Unknown or external agent fallback
const chatUnknown = "- [2026-09-28 17:06:00] [External Auditor]: Review complete.";
const parsedUnknown = parseChatLog(chatUnknown);
assert(
  parsedUnknown.length === 1 && parsedUnknown[0].meta.role === 'AGENT',
  'Unknown agent defaults safely to generic AGENT role with robot avatar'
);

// 6.8 Delimiter without space after colon
const chatCompact = "- [2026-09-28 17:07:00] [Worker 1]:NoSpaceMessage";
const parsedCompact = parseChatLog(chatCompact);
assert(
  parsedCompact.length === 1 && parsedCompact[0].rawText === 'NoSpaceMessage',
  'Compact colon delimiter (- [...][...]:Message) parsed accurately'
);

// 6.9 Catastrophic ReDoS stress test on DELIMITER_REGEX
const maliciousDelimiter = '- [' + '9'.repeat(5000) + '] [' + 'A'.repeat(5000) + ']' + ': '.repeat(100);
const startRedos = Date.now();
parseChatLog(maliciousDelimiter);
const redosDuration = Date.now() - startRedos;
assert(
  redosDuration < 50,
  `ReDoS resistance: 10,000 char malformed delimiter parsed in ${redosDuration}ms (<50ms)`
);


// ─────────────────────────────────────────────────────────────────────────────
// SUMMARY & VERDICT
// ─────────────────────────────────────────────────────────────────────────────
console.log('\n================================================================');
console.log(' EMPIRICAL TEST SUMMARY');
console.log('================================================================');
console.log(`Total tests executed : ${totalTests}`);
console.log(`Passed               : ${passedTests}`);
console.log(`Failed               : ${failedTests}`);

if (failedTests > 0) {
  console.log('\nIdentified Bugs / Failure Modes:');
  failures.forEach((f, i) => console.log(`  ${i + 1}. ${f.testName}\n     ${f.details}`));
  console.log('\nFINAL EMPIRICAL VERDICT: REJECT ❌ (Actionable findings identified)');
  process.exit(1);
} else {
  console.log('\nFINAL EMPIRICAL VERDICT: APPROVE ✅ (100% Passed)');
  process.exit(0);
}
