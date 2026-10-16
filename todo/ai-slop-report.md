# AI Slop Report

**Round 3 (OPEN):** [`src/core/`](#round-3--srccore-open) — 46 `.js` files · audited 2026-09-26
**Guide:** [`ai-slop-inspection-guide.md`](ai-slop-inspection-guide.md)

> **Note on this file's git state.** `todo/ai-slop-report.md` was already deleted in the working tree
> when this round began (unrelated to this audit). Round 2's content is intact in `HEAD`
> (`git show HEAD:todo/ai-slop-report.md`). This file is a fresh Round 3; the Round 2 section was not
> restored, to avoid silently reintroducing content the author removed. Restore it with
> `git checkout HEAD -- todo/ai-slop-report.md` and merge if you want the history kept in one place.

---

# Round 3 — `src/core/` (OPEN)

**Audit date:** 2026-09-26 · **Guide:** [`ai-slop-inspection-guide.md`](ai-slop-inspection-guide.md)
**Scope:** `src/core/` — 46 `.js` files, ~14,400 lines (incl. `Account/`, `analytics/`, `financial-planning/`)
**Method:** Rule-by-rule sweep with `rg`, then per-finding line re-derivation, a repo-wide grep for every
"dead" claim (filename + exported symbol + non-`.js` files), a computed-`import()` check, and — for both
headline findings — a **throwaway Vitest probe** that was run, captured, and then deleted.

**Baseline:** `yarn vitest run` → **574/574 passing (83 files)** on `master` before this round. Every
finding below was found by reading code, not by a red test. No pre-existing failures to attribute.

---

## Executive summary

The round found **two real, user-visible defects that the test suite cannot see**, both in the
"telemetry that nobody watches" tier of the codebase — which is exactly where an audit earns its keep,
because nobody reviews `click-tracking-service.js` and everybody is relying on `account-deletion-service.js`.

The headline is that **GDPR account deletion does not delete budgets or investments, and the defect is
swallowed into a `warnings` array** — so the flow still reports a specific, verified failure rather than
lying. The second is that **a single corrupt `localStorage` value makes BlinkBudget silently stop
saving the user's transactions**, because a throw in a click-counting service fires _before_ the
`TransactionService.add()` that was supposed to record the expense. That is data loss, and it is the
only finding in this report that reaches the top severity tier.

| Bucket                      | Count | Notes                                                                                    |
| --------------------------- | ----- | ---------------------------------------------------------------------------------------- |
| 🔴 High (real defect)       | 2     | #4.1 deletion leaves data behind (🔒); #4.2 corrupt value stops transactions being saved |
| 🟡 Medium                   | 8     | #4.3 listener leaks, #15.1 unawaited `invalidate` contract, #3.1 `null` history shape, … |
| ⚪ Low                      | 4     | #5.1 dead `\|\|` fallback, #11.1 tombstone comment, #2.1/#2.2 logs, #7.2 unbound `catch` |
| **User Review Required**    | 1     | #4.1 — 🔒 security-sensitive; never auto-remediated in the pass that found it            |
| False positives (corrected) | 4     | Recorded below; each downgraded _after_ verification, not assumed                        |

> **Calibration note.** One finding was filed 🔴 on first pass and **re-ranked down** after
> re-derivation — see [#4.3](#43--four-event-listeners-that-destroy-can-never-remove-) and the
> "Re-ranks" section. The guide requires the correction be recorded rather than silently applied.

---

## The one thing that matters

**A corrupt value in one telemetry key silently stops BlinkBudget from saving expenses.**

`click-tracking-service.js` is a click counter. It has no bearing on whether a transaction is saved.
But `AddView.js` calls it at line 83 — _before_ the `try { TransactionService.add(data) }` at line 86 —
so any throw inside the counter aborts the submit handler before the user's expense is ever recorded.
The trigger is a truncated `blinkbudget_click_tracking` value, which is entirely ordinary: the key is
written by `saveHistory()` on every completed transaction and read back through `safeJsonParse`, whose
**documented contract is to return `null` on malformed input**. The guard at line 135 checks whether the
_raw string_ is falsy; it never checks the _parse result_.

This is worth stating plainly because the consequence is inverted from what the code looks like: a
**background metric** has become a **hard dependency of the core 3-click logging path**, and nothing in
the code, the tests, or the type-free JS makes that dependency visible.

---

## Findings

### Rule #2 — Swallowed errors

#### #2.1 — Misleading diagnostic on a handled `safeJsonParse` null ⚪ Low

- **File:** `src/core/navigation-state.js:76-103`
- **Severity:** ⚪ Low
- **Snippet:**
  ```js
  const timePeriodData = safeJsonParse(savedData);   // L76 — may be null
  if (!timePeriodData.startDate || !timePeriodData.endDate) {   // L79 — TypeError when null
  ```
- **Mechanism verified against source:** `safeJsonParse` returns `null` on malformed input
  (`security-utils.js:99-102`). L79 then dereferences it, throwing inside the `try` opened at L70, which
  lands in the `catch` at L102. **The behaviour is correct** — the function returns `null` either way —
  but the log reads `"[NavigationState] Failed to restore time period: TypeError: Cannot read properties
of null"`, which points a future debugger at a restore bug when the real cause is one corrupt
  `sessionStorage` value.
- **Verdict:** slop (guard on the wrong variable, same family as #4.2) — but the failure mode is a
  misleading log line, not a crash.
- **Action:** **flagged for follow-up.** `if (!timePeriodData?.startDate …)`.

#### #2.2 — Load failure logged twice, once as a wrapped error ⚪ Low

- **File:** `src/core/chart-loader.js:39-44` and `120-129`
- **Severity:** ⚪ Low
- **Rule #:** 2, 7
- **Verdict:** slop. `loadChartJSModules` logs the failure with timing at L122 and then throws a
  **wrapped** `new Error('Failed to load Chart.js: …', { cause })` at L126; the caller's `catch` at L39
  logs that wrapper again. One failure, two console entries, and the caller's log line names a
  "ChartLoader" failure whose cause is two frames down. Re-throwing is correct here; the duplicate log is not.
- **Action:** **flagged for follow-up.**

### Rule #3 — Overly defensive / dead guards

#### #3.1 — `InvestmentTracker.getAllInvestments ? … : []` masks the #4.1 bug 🟡 Medium

- **File:** `src/core/Account/account-deletion-service.js:673-676`
- **Severity:** 🟡 Medium
- **Snippet:**
  ```js
  const investments = InvestmentTracker.getAllInvestments
    ? InvestmentTracker.getAllInvestments()
    : [];
  ```
- **Mechanism verified against source:** `InvestmentTracker.getAllInvestments` is `undefined` (class
  static does not exist — see #4.1), so the ternary **always** takes the `[]` branch. Probe:
  `PROBE_SUMMARY={"transactions":0,"accounts":1,"goals":0,"investments":0,"budgets":1,…}` — the user had
  one investment seeded and the "data summary before deletion" screen told them **0**.
- **Verdict:** slop, and it is the more interesting half of #4.1. Note the **inconsistency within one
  file**: L195 calls the same missing static with **no** guard (throws → warning), while L673 guards it
  (silently wrong answer). The guard is not dead in the harmless sense — it actively **converts a loud
  failure into a wrong number** shown to the user on the deletion confirmation screen.
- **Action:** **flagged for author confirmation** — folded into the 🔒 #4.1 work, not remediated here.

### Rule #4 — Dead / unreachable code

#### #4.1 — GDPR account deletion leaves budgets and investments in localStorage 🔴 High

- **File:** `src/core/Account/account-deletion-service.js`
- **Line(s):** 194–203 (investments), 211 (budgets), 473–479 (investment verification), 673–676 (summary)
- **Severity:** 🔴 High
- **Rule #:** 4, 3, 2
- **Snippet:**
  ```js
  const { InvestmentTracker } = await import('../investment-tracker.js');   // L194
  const investments = InvestmentTracker.getAllInvestments();                // L195  ← not a static
  for (const investment of investments) {
    InvestmentTracker.removeInvestment(investment.id);                     // L198  ← not a static, wrong arg
  ...
  const { BudgetService } = await import('../budget-service.js');          // L207
  BudgetService.deleteBudget(budget.id);                                   // L211  ← method is `delete`
  ```
- **Mechanism verified against source:** `InvestmentTracker` is exported as a **class**
  (`investment-tracker.js:13`) whose `getAllInvestments` (`L177`) and `removeInvestment` (`L118`) are
  **instance** methods; the only static export is the `investmentTracker` singleton (`L357`).
  `BudgetService` is an **object literal** (`budget-service.js:14`) exposing `delete(id)` at `L71` — there
  is no `deleteBudget` on it. The real bridge name is `StorageService.deleteBudget` (`storage.js:196`),
  which is what `BudgetsSection.js:285,318` uses. A runtime probe confirmed all three as `undefined`:
  ```
  "InvestmentTracker.getAllInvestments": "undefined"
  "InvestmentTracker.removeInvestment":   "undefined"
  "BudgetService.deleteBudget":           "undefined"
  "BudgetService.delete (real name)":     "function"
  ```
  And confirmed the **consequence**, not just the mechanism — with one budget and one investment seeded:
  ```
  PROBE_WARNINGS=["Investment deletion failed: InvestmentTracker.getAllInvestments is not a function",
                  "Budget deletion failed: BudgetService.deleteBudget is not a function"]
  PROBE_REMAINING_BUDGETS=[{"id":"b1","categoryName":"Food"}]   ← survived the "delete" step
  ```
- **Verdict:** slop — a wrong assumption about a module's shape, mechanically identical to the
  `InvestmentTracker.getAllInvestments ? … : []` guard at L673 which the guide already names as a
  reference example of a dead defensive guard. The same bug appears in four places (L195, L198, L211, L474).
- **Consequence, stated accurately:** the failures are caught per-block and pushed to `result.warnings`,
  so the user **is** eventually told — `stepVerifyDeletion` re-reads the data, finds the budget still
  present, and pushes `"Account deletion verification failed"`. So this is **not** a silent failure and
  **not** a false success report. It is: _the data the user asked to be permanently erased is not
  erased_, and the user gets a generic verification-failure string instead of "your budget could not be
  deleted". That is a data-retention failure against an explicit erasure request, which is why it stays
  🔴 despite the user-visible signal existing.
- **Action:** The fix is
  `BudgetService.delete(budget.id)` and `investmentTracker.getAllInvestments()` /
  `.removeInvestment(investment.symbol)` (note: **symbol**, not `id` — `removeInvestment` is documented at
  `investment-tracker.js:118` as taking a symbol, so L198 has a _second_, independent bug behind the
  first).

#### #4.2 — A corrupt telemetry value stops transactions from being saved 🔴 High

- **File:** `src/core/click-tracking-service.js`
- **Line(s):** 18, 135, 59, 95, 124 · **Reachability:** `src/views/AddView.js:83`, `src/components/TransactionList.js:69,103`
- **Severity:** 🔴 High
- **Rule #:** 4, 2
- **Snippet:**
  ```js
  this.history = this.loadHistory();                 // L18  — constructor
  ...
  return stored ? safeJsonParse(stored) : [];        // L135 — guards the STRING, not the PARSE RESULT
  ...
  this.history.push(metrics);                        // L59  — throws when history === null
  if (this.history.length === 0) {                   // L95  — throws when history === null
  ```
- **Mechanism verified against source:** `safeJsonParse` returns `null` on malformed JSON —
  `security-utils.js:99-102`, `catch { … return null; }`, and the JSDoc at `:67` states
  _"The parsed object **or null if parsing fails**"_. The `? :` at L135 only substitutes the default when
  `stored` is falsy; when `stored` is a corrupt **non-empty string**, the truthy branch runs and returns
  `null`, which is assigned straight to `this.history`. A runtime probe with a single truncated value
  (`'{"clicks":3,'`) confirmed:
  ```
  PROBE_HISTORY=null
  PROBE_GET_AVERAGE=THREW: Cannot read properties of null (reading 'length')
  PROBE_COMPLETE_FLOW=THREW: Cannot read properties of null (reading 'push')
  ```
- **Why this is 🔴 and not a nit:** the throw is not contained. `AddView.js` calls
  `ClickTracker.completeTransactionFlow()` at **line 83**, and the `try { TransactionService.add(data) }`
  that actually records the expense begins at **line 86**. The throw therefore propagates out of the
  submit handler _before the user's transaction is written_. A user who logs one expense with a
  truncated telemetry key **loses that expense**, and — because `completeTransactionFlow` is called
  unconditionally at the top of `onSubmit`, not just on success — the next attempt throws at the same
  place, so it is **not self-healing**. `TransactionList.js:69` throws too, so the list will not render.
- **Verdict:** slop. The specific error is a guard that checks the wrong thing — the classic
  "null check on a value that was already validated two lines above" shape, except validated against the
  wrong variable.
- **Action:** **flagged for follow-up** (not fixed this session — this round is audit-only; no source
  files were modified). Fix is one line: `const parsed = safeJsonParse(stored); return Array.isArray(parsed) ? parsed : [];`
  Regression test must go in a **new** file: `tests/setup.js` replaces `localStorage` with bare
  `vi.fn()` spies whose `getItem` returns `undefined` for every key, so a suite that asserts on stored
  history needs a real in-memory store installed locally (guide, testing trap #6). The revert run should
  fail with the `Cannot read properties of null` TypeError, not an assertion mismatch.

#### #4.3 — Four event listeners that `destroy()` can never remove 🟡 Medium _(re-ranked down)_

- **File:** `src/core/mobile-utils.js`
- **Line(s):** 65–68 + 76–80, 118–119 + 121–125, 151, 158 + 159–163; cleanup at 569–585
- **Severity:** 🟡 Medium — **filed 🔴, re-ranked down after re-derivation**
- **Rule #:** 4
- **Snippet:**
  ```js
  window.visualViewport.addEventListener('scroll', this.handleViewportScroll.bind(this));  // L67
  this.eventListeners.set('viewport-scroll', {                                           // L76
    target: window.visualViewport, event: 'scroll',
    handler: this.handleViewportScroll.bind(this),   // L79 — a DIFFERENT function object
  });
  ...
  document.addEventListener('focusin', this.optimizeInput.bind(this));   // L151 — never stored at all
  document.addEventListener('keydown', this.handleKeyDown.bind(this));   // L158
  this.eventListeners.set('keydown', { handler: this.handleKeyDown.bind(this) });  // L162
  ```
- **Mechanism verified against source:** `Function.prototype.bind` returns a **new** object on every
  call and is not memoised, so `x.bind(this)` at L67 and `x.bind(this)` at L79 are distinct references.
  `destroy()` (L569–574) can only remove what is in `eventListeners`, so it removes the L79 copy and
  leaves the L67 copy attached to `window.visualViewport` forever. Same defect at L158/L162 for `keydown`.
  L151 is never recorded at all. Separately, L118–119 registers `handleOrientationChange` on **both**
  `orientationchange` and `resize`, but L121 only records the `orientationchange` one — the `resize`
  registration is also unreachable by `destroy()`.
- **Why the severity was lowered:** the leak is real, but `MobileUtils.initialize()` is called exactly
  once per page load, from `main.js:22`. Within a single SPA session the listeners accumulate once and
  stop. This is **not** an unbounded in-session leak; it is a broken removal contract that only bites if
  `initialize()` is ever called again. Reporting it as 🔴 would have overstated a latent defect as an
  active one.
- **Verdict:** slop. The cleanup scaffolding is genuine and the intent is clear — the giveaway is that
  `initialize()` at L591–593 calls `destroy()` _specifically to avoid duplicate listeners_, so the
  contract is load-bearing by the code's own admission, and it does not hold.
- **Action:** **flagged for follow-up.** Fix is to bind once into a local (`const onScroll = this.handleViewportScroll.bind(this)`), register that, and store that same reference; add the L151 handler to the map.

### Rule #5 — Hardcoded values / design-system bypass

#### #5.1 — Dead `||` fallback over a token that exists with the identical value ⚪ Low

- **File:** `src/core/click-tracking-service.js:133,148`
- **Severity:** ⚪ Low
- **Snippet:**
  ```js
  STORAGE_KEYS.CLICK_TRACKING || 'blinkbudget_click_tracking';
  ```
- **Mechanism verified against source:** `STORAGE_KEYS.CLICK_TRACKING` is defined at
  `src/utils/constants.js:308` with the value `'blinkbudget_click_tracking'` — **the same string**. The
  left operand is a non-empty literal, so the right operand is unreachable. This is the guide's rule #5
  "fallback literals in `|| N` chains", and it is actively misleading: a reader greps the hardcoded
  string, assumes the key might be undeclared, and misses that the token is the single source of truth.
- **Verdict:** slop. **False-positive check applied:** I did _not_ assume the token existed — the guide
  requires grepping for the definition, which is what surfaced `constants.js:308`. Had it been absent,
  the fallback would have been load-bearing and correct to keep.
- **Action:** **flagged for follow-up** — drop the `||` and use the token.

### Rule #6 — Indirection with zero added logic

#### #6.1 — Three `async` pass-throughs over synchronous storage 🟡 Medium

- **File:** `src/core/savings-goals-service.js:13-35`
- **Severity:** 🟡 Medium
- **Snippet:**
  ```js
  static async getSavingsGoals() {
    const { StorageService } = await import('./storage.js');
    return StorageService.getGoals() || [];
  }
  ```
- **Verdict:** slop. Three methods (`getSavingsGoals`, `saveSavingsGoal`, `deleteSavingsGoal`) each add a
  dynamic import and nothing else. The `async` is not incidental: `StorageService.getGoals()` is
  **synchronous** (`storage.js:127`), so the `async`/`await` here is a promise the caller must await for
  no reason, propagating an artificial async boundary to `GoalsSection.js`. The only justification is
  lazy-loading `storage.js`, which `GoalsSection` already imports directly.
- **Action:** **flagged for follow-up** — drop `async`/`await` and keep the import, or inline.

### Rule #7 — Inconsistent error-handling patterns

#### #7.1 — Raw `JSON.parse` where the codebase standard is `safeJsonParse` 🟡 Medium

- **File:** `src/core/amount-preset-service.js:26` (+ `:104`, `:166`)
- **Severity:** 🟡 Medium
- **Snippet:**
  ```js
  return data ? JSON.parse(data) : { amounts: {}, presets: [] };   // L26
  ...
  if (!presetsData.amounts[normalizedAmount]) {                    // L104
  ```
- **Mechanism verified against source:** every other core service that parses storage uses
  `safeJsonParse` — `account-service.js:26`, `budget-service.js:21`, `investment-tracker.js:298`,
  `settings-service.js:30,51`, `sync-service.js:469,488`, `click-tracking-service.js:135`.
  `amount-preset-service.js` is the outlier, and it is the **only one of these that has the same null
  hole as #4.2**: a stored `"null"` makes `data` truthy, `JSON.parse` returns `null`, and L104
  dereferences `presetsData.amounts` → TypeError inside `recordAmount`, reached from
  `analytics-engine.js:135`. Not runtime-probed (the two share a mechanism, not a call site), so filed as
  🟡 on the code read plus the shared root cause.
- **Verdict:** slop. A sibling-file comparison (rule #7) is what surfaced this — one service in a
  directory of nine that solves the same problem the hard way.
- **Action:** **flagged for follow-up** — same one-line fix as #4.2.

#### #7.2 — Unbound `console.warn` passed straight to `.catch()`, three times ⚪ Low

- **File:** `src/core/auth-service.js:150,212,287`
- **Severity:** ⚪ Low
- **Snippet:** `this._updateUserProfile(this.user).catch(console.warn);`
- **Verdict:** slop. `console.warn` is passed **unbound**; it works in practice only because console
  methods are bound in modern engines. The comment `// Update profile in background` is repeated
  verbatim three times and, per rule #1, adds nothing the code does not already say. The error contract
  is otherwise documented well at L112.
- **Action:** **flagged for follow-up** — `.catch(err => console.warn('[AuthService] profile update:', err))`.

### Rule #9 — Duplicated logic

#### #9.1 — `topMovers` and `categoryExpenseTotals` are the same loop 🟡 Medium

- **File:** `src/core/insights-generator.js:15-47` and `52-65`
- **Severity:** 🟡 Medium
- **Verdict:** slop. Both filter on `type === 'income' | 'transfer' | 'isGhost`, both normalise
  `amount` the same way, both apply the same `refund → -Math.abs` adjustment — ~80% identical bodies
  differing only in the accumulator shape (`{total,count}` object vs `Map<number>`). The file's own
  comment at L50–51 says _"Same rules as topMovers"_, i.e. the duplication is acknowledged but not
  factored. A shared `iterExpenseCategories(transactions)` generator would collapse both.
- **Action:** **flagged for follow-up.**

### Rule #11 / #1 — Stale and trivial comments

#### #11.1 — Tombstone comment about a browser that predates the event ⚪ Low

- **File:** `src/core/install.js:19` — `// Prevent Chrome 67 and earlier from automatically showing the prompt`
- **Verdict:** stale. `beforeinstallprompt` shipped in Chrome 72; in Chrome 67 the event never fired, so
  the handler could not have been about "Chrome 67 and earlier". The `e.preventDefault()` itself is
  correct per spec. Under rule #1's archaeology caveat this is _not_ a candidate for deletion on the
  "restates the code" basis — but the claim it makes is checkably false, so it is filed under #11.
- **Action:** **flagged for follow-up** — rewrite to state the spec requirement.

#### #11.2 — Fix-narrative comment with no ticket 🟡 Low

- **File:** `src/core/savings-goals-service.js:87` — `// Fix monthlySavingRate calculation to use actual time span`
- **Verdict:** per the guide's archaeology rule this is **evidence, not noise** — it is the only record of
  _why_ the code below computes min/max timestamps instead of a naive average. **Do not delete.**
  Recommend rewording to a rationale ("compute over the actual span, not a fixed 30 days") rather than
  removal, since as written it reads as a changelog entry.
- **Action:** **left as-is** (reason: archaeology — see above).

### Rule #14 — File / module bloat

#### #14.1 — 12 of 46 core files exceed the 500-line guideline 🟡 Medium

- **Severity:** 🟡 Medium
- **Verdict:** per rule #14, flagged as its own follow-up and explicitly **not** bundled into a slop
  cleanup. `AGENTS.md` sets 500 lines. Over the limit:

  | Lines | File                                  |
  | ----: | ------------------------------------- |
  |   962 | `data-integrity-service.js`           |
  |   857 | `custom-category-service.js`          |
  |   823 | `analytics/TrendService.js`           |
  |   770 | `forecast-engine.js`                  |
  |   714 | `sync-service.js`                     |
  |   712 | `Account/account-deletion-service.js` |
  |   712 | `chart-config.js`                     |
  |   704 | `backup-service.js`                   |
  |   655 | `goal-planner.js`                     |
  |   647 | `analytics/AnomalyService.js`         |
  |   615 | `navigation-state.js`                 |
  |   609 | `mobile-utils.js`                     |

- **Note:** `account-deletion-service.js` is where #4.1 lives, and its bulk is 20 near-identical
  `try { … } catch (error) { result.warnings.push(...) }` blocks. The repetition is also why the bug
  survived — each block is a near-copy, so a wrong method name in one is invisible in the other nineteen.
- **Action:** **flagged for follow-up** — split as its own change, per rule #14.

### Rule #15 — Async boundary bugs

#### #15.1 — `analyticsCache.invalidate()` is `async` and awaited nowhere — 12 call sites 🟡 Medium

- **File:** `src/core/cache-invalidator.js:32,33,66` · `src/core/storage.js:69,83,92,101,138,156,162,168,174`
- **Severity:** 🟡 Medium
- **Rule #:** 15A, 15B, 7
- **Snippet:**
  ```js
  // cache-invalidator.js — all inside one try/catch whose catch is at L73
  analyticsCache.invalidate('portfolioSummary');   // L32
  analyticsCache.invalidate('goalsSummary');        // L33
  analyticsCache.invalidate('forecast_');           // L66
  window.dispatchEvent(new CustomEvent('forecast-invalidate', { … }));   // L67-70
  ```
- **Mechanism verified against source:** `invalidate` is declared `async` at `AnalyticsCache.js:403`, so
  it **returns a promise and cannot throw synchronously** — the `try`/`catch` at L26/L73 is structurally
  incapable of observing its failure. The body `await this._acquireLock()` at L404 before touching
  anything, so the deletions have not happened by the time L67 dispatches the invalidation event
  synchronously. This is the guide's own reference example, and the _correct_ pattern is visible **13
  lines above it in the same function** (L38–55: capture keys with `getMatchingKeys`, then
  `invalidateSync` + `invalidate(pattern, keys)`).
- **What I did _not_ verify (stated so the author can finish the job):** the ACCOUNTS branch's
  `handleForecastInvalidate` listener (`FinancialPlanningView.js:534`) calls `forecastEngine.clearCache()`
  and then `renderSection(currentSection)` **synchronously**. Whether that re-render actually reads a
  `'forecast_'`-prefixed key from `analyticsCache` in the same tick — and therefore observes stale data —
  I did **not** trace. The ordering defect is verified; the stale read is not. Per the guide, a finding
  whose mechanism cannot be completed is not ready to file as a race, so this is filed as a contract
  defect, not as a confirmed race.
- **Verdict:** slop. Written up **once** as a contract problem across all 12 call sites rather than as 12
  findings (guide, rule #15D). The `storage.js` sites are the same shape: nine synchronous bridge methods
  (`addInvestment`, `addGoal`, `updateGoalProgress`, …) that invalidate the cache and return before the
  invalidation completes.
- **Action:** **flagged for follow-up.** The right fix is the one the codebase already demonstrates at
  `cache-invalidator.js:38-55` — capture the keys, `invalidateSync` for same-tick visibility, then the
  async pass — applied once in a helper both files call, rather than hand-editing 12 sites.

---

## Re-ranks (recorded, not silently corrected)

The guide requires a re-rank be written down rather than quietly fixed. One, in each direction:

- **Narrowed:** #4.3 (mobile-utils listener leaks) was filed 🔴 on the strength of four un-removable
  listeners and a `destroy()` that `initialize()` explicitly depends on. Re-deriving the call sites showed
  `initialize()` runs **once per page load**, so the listeners accumulate once and stop. Downgraded to 🟡.
  Filed as latent, not active.
- **Not narrowed, and the opposite error was caught:** #4.2 was initially a 🟡 — "a telemetry service can
  throw on corrupt data" sounds cosmetic. Reading the _call site_ rather than trusting the finding's
  framing turned it into the round's worst defect: `AddView.js:83` runs **before** the `try` at `:86` that
  saves the transaction, so the user loses the expense and the failure repeats on every retry. Severity
  raised 🟡 → 🔴 **because the mechanism, on re-reading, turned out to be worse than first written** —
  not because the original prose was confident. A finding whose stated mechanism does not survive contact
  with the code must be re-scoped before it is filed; this one did not survive, and re-scoping it is what
  produced the finding.

---

## False positives — downgraded after verification

Recorded because a round with zero false positives is itself a signal of pattern-matching too fast.

1. **"Six analytics services are orphans."** `CategoryUsageService`, `PredictionService`,
   `ComparisonService` and `FilteringService` all appeared to have **zero** references outside their own
   files. They are all imported and called by `analytics-engine.js:10-18,29-193`. My first grep excluded
   `src/core/` paths to find "external" callers, which excluded the very file that uses them. **The
   service was alive; the grep was wrong.** No finding filed.
2. **"Every `src/core` export is dead."** A first pass over all 57 exports reported every one as
   zero-reference. Cause: `rg -o --replace` retained the `file:line:` prefix, so each symbol was searched
   as the literal string `src/core/foo.js:12:Bar`. Re-ran with `--no-filename`; the real result is
   **zero** dead exports, which is a genuinely clean result rather than the catastrophe the first pass
   implied.
3. **"`accessibleColors` / `chartColors` are dead exports."** Grep found them only in their own file — but
   they are consumed **internally** by `getChartColors` at `chart-config.js:360-387`. Live. Only their
   `export` keyword is superfluous, which is not a finding.
4. **"`accountDeletionService` is unused."** It has exactly one caller, `AccountDeletionSection.js`, plus
   two tests. Thin, but live. No finding filed.

Two further checks came back clean and are recorded as **evidence, not findings**: there are **no computed
dynamic imports** in `src/` (rule #4 step 3 — the only non-relative `import()` is the literal `'chart.js'`,
so a filename grep cannot be defeated by a runtime-built path), and there are **no module-load side
effects** in `src/core` (rule #8 — no top-level `addEventListener`, no `document.*` at module scope; the
`MobileUtils` constructor's `this.init()` runs only under an explicit `initialize()` call from `main.js:22`).

---

## Traceability — every finding has a destination

| Finding                                      | Severity | Destination                                                   |
| -------------------------------------------- | -------- | ------------------------------------------------------------- |
| #4.1 deletion leaves data behind             | 🔴       | Phase 1 - approved by author                                  |
| #3.1 null-guard masks #4.1                   | 🟡       | Rolled into the 🔒 #4.1 work                                  |
| #4.2 corrupt value stops transactions saving | 🔴       | Phase 1 — one-line fix + new regression test                  |
| #4.3 listener leaks                          | 🟡       | Phase 1 — bind once, register and store the same reference    |
| #15.1 unawaited `invalidate` ×12             | 🟡       | Phase 2 — one shared helper, not 12 edits                     |
| #7.1 raw `JSON.parse` in presets             | 🟡       | Phase 1 — same fix as #4.2                                    |
| #2.1 misleading restore log                  | ⚪       | Phase 1 — optional chaining                                   |
| #2.2 double-logged chart load failure        | ⚪       | Phase 3                                                       |
| #5.1 dead `\|\|` fallback                    | ⚪       | Phase 1                                                       |
| #6.1 async pass-throughs                     | 🟡       | Phase 3                                                       |
| #7.2 unbound `console.warn` ×3               | ⚪       | Phase 3                                                       |
| #9.1 duplicated insights loop                | 🟡       | Phase 3                                                       |
| #11.1 Chrome 67 comment                      | ⚪       | Phase 3                                                       |
| #11.2 fix-narrative comment                  | ⚪       | **Left as-is** — archaeology, rewrite not remove              |
| #14.1 12 files over 500 lines                | 🟡       | **Own follow-up** — never bundled into a slop pass (rule #14) |

**Phases above are proposed, not executed.** This round was audit-only: no file under `src/` was
modified. The two probe files (`tests/core/zz-probe-*.test.js`) were created, run, and deleted; the
working tree contains no changes from this audit.

**Verification plan when the fixes land**

- Run the suites covering every touched file, not only the highest-severity fix.
- **Prove each regression test fails without its fix** (guide, testing trap #2). For #4.2 the revert must
  fail with `TypeError: Cannot read properties of null (reading 'push')` — the actual user symptom — not
  an assertion mismatch. For #4.1 seed the regression with recognisable sentinels so the reverted failure
  output _shows the retained data_ (e.g. `expected [ { id: 'b1', categoryName: 'Food' } ] to be null`).
- Regression tests for #4.2 and #7.1 must go in **new** files with a real in-memory `localStorage`
  installed locally; the shared `tests/setup.js` double is a bare stub and can never express the
  assertion. Do not modify `setup.js` — it would move the baseline for every other suite.
- Run lint/format/build once per phase.
- Re-grep for newly orphaned code after each deletion — #4.1's fix may leave `getUserDataSummary`'s
  import chain or an `||` fallback unused.
- Manual QA for #4.1, as a concrete user action: _"Settings → Delete account on an account holding one
  budget and one investment; the confirmation summary must show 1 and 1, and after deletion a fresh
  account must have neither."_ This one is a human decision, not a code change.
