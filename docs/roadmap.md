# Tokenograph: a big-picture lens and optional path to sharing

**Accepted internal direction · 2026-09-05.** The owner adopted this as the default
working assessment and roadmap. It is not a launch announcement or a claim of validated
demand. No publication, license choice, or hosted service is authorized by this document.

## How to use this roadmap

The owner clarified that there is no short-term urgency to promote or release the tool.
This is a map, not a schedule: make the instrument useful in our own work first. The
sharing milestones below describe an optional path, not the immediate work queue.

The starting signal was recognition: seeing the original screenshot gave shape to a tool
the owner already wanted. That is a sound reason to build and a reasonable hypothesis that
others doing similar work will want it too. Audience size and which features matter most
remain open questions; they need not be resolved before improving the personal tool.

Near-term priorities should come from actual sessions: trustworthy accounting, easier
interpretation, and the questions the current panel leaves unanswered. Keep the privacy
and distribution checklist ready for whenever sharing becomes desirable.

## The opportunity

**A local flight recorder for coding agents: see where the context, time, and money went.**

Token totals alone are easy to get. Tokenograph's more useful questions are: what filled
the window, what was repeatedly sent, when did a cached prefix disappear, and which tool
traffic dominated? The context ledger and cache explanations should be the product's
center, with cost and throughput as supporting evidence.

The first audience is developers who run long Claude Code, Codex CLI, or pi sessions and
already wonder why they got slower, larger, or more expensive. Harness and memory-tool
builders are a promising second audience. Do not initially market this as billing
software, a model leaderboard, or a measure of agent quality.

**My recommendation: release it as a local, open-source developer tool first.** The
existing implementation justifies a small beta. It does not yet establish market size,
repeat use, willingness to pay, or savings on other people's workflows.

## What exists, and what still blocks a release

Verified in this checkout:

- Three adapters, a shared ledger and timeline, live and static panels, fleet integration,
  and JSON/GraphML/CSV exports. Python standard library at runtime.
- The two review defects are fixed: late Claude metadata survives; timestamped Codex
  activity is retained independently of confirmed usage. Tokens, cost, and context still
  use only confirmed Codex usage. Graph ledger links remain aligned; incomplete usage
  is disclosed in the footer and suppresses misleading throughput. Synthetic coverage
  includes static/live views, later confirmed calls, provisional usage, tool errors,
  and additive timing (44 tests currently pass).
- A wheel builds, includes both HTML templates, installs into a fresh virtual environment,
  and runs `json` and `build` on the synthetic Codex fixture outside the checkout. This
  smoke check used Python 3.14 on macOS, not the full declared Python >=3.8 range.
- No tracked license, CI workflow, contribution guide, or security policy was found.
  Generated `build/lib/` copies remain tracked despite `build/` being ignored.
- Exported data contains free-text prompt/tool labels and identifying paths. There is no
  sharing-safe export mode yet. Both pages request Google Fonts; local analysis is not
  the same as a browser page making zero external requests.
- `__init__.py` contains scanning, analysis, serving, and exports. The live reader appends
  incrementally, but analysis rebuilds from retained entries. Large-session behavior needs
  a reproducible benchmark before promising fleet-scale use.

A complete UI is not the release gate. **Trust, privacy, installation, and a clear first
use are.** Keep the current CLI working throughout the roadmap.

## Milestone 1 — trustworthy and safe to try

**Conditional priority: when preparing to share. Scope: release blockers, not a deadline.**

1. **Establish distribution rights.** Review imported source/history and bundled assets;
   have the owner select a license and preserve required attribution. I would default to
   MIT for broad reuse; choose Apache-2.0 if an explicit patent grant is important. Do not
   publish a package without making this choice and setting matching package metadata.
2. **Make missing data explicit.** Separate observed activity from usage coverage in the
   exported schema and panel: measured, estimated, unavailable, and partial. Extend the
   current unmetered-call count/footer disclosure into per-field UI treatment. Show a
   confirmed-call count beside partial token/cost totals. Do not display missing usage as
   measured zero, or unknown compaction duration as measured zero seconds. Derive
   throughput from a matched set of tokens and durations, or show it as unavailable when
   coverage is insufficient. A point marker remains useful even when duration is unknown.
3. **Add a sharing-safe export boundary.** Analyze locally first, then create an allowlisted
   export payload. Preserve numeric measurements, sizes, relationships, and relative
   times; replace free text and identifying metadata with neutral labels/opaque IDs.
   Include titles, prompts, commands, paths, source filenames, session IDs, branch names,
   custom tool/schema names, error text, ledger subsection labels, and fleet metadata.
   Apply the same policy to embedded HTML JSON, JSON, GraphML, and CSV. CSS hiding and
   secret-pattern regexes are not a privacy boundary. A sanitized artifact is not a
   guarantee of anonymity; timestamps and distinctive activity can still identify a run.
4. **Keep raw reports explicitly private.** Document that existing reports can reveal
   session content. Use synthetic examples for the demo and issue templates. Review
   existing tracked reports and history before wider promotion; do not rewrite history
   without owner approval. A future fixture scrubber must preserve lengths and event
   structure rather than changing the measurements through naive text replacement.
5. **Make local really local.** Use system fonts or license-reviewed bundled fonts. Keep
   loopback serving as the default; document that the development server has no
   authentication and is not intended for internet exposure. Audit unsafe HTML/CSV output
   and unusual/malformed transcript input; never execute transcript commands.
6. **Automate the contract.** CI runs unit tests, synthetic invariant checks, wheel install
   smoke tests, and a browser smoke check. Choose and test a supported Python/OS matrix;
   either verify the declared minimum or raise it explicitly. Add hand-built fixtures for
   replay/resume, duplicate counters, resets, interleaved tools, incomplete records,
   truncation, model changes, and unknown formats. Remove tracked build copies in a
   separate cleanup change; install only the actual package.

**Exit gate:** supported environments install and run a demo without a maintainer;
confirmed counters reconcile; wall time stays additive; ledgers stay bounded; missing
measurements are visibly missing; seeded secrets are absent from every sharing-safe
output byte; the rendered demo makes no automatic external requests. Commit only
synthetic fixtures, never raw user transcripts.

## Milestone 2 — small public beta, then evidence of repeat use

**Dependency: milestone 1. Keep the release deliberately small.**

- Ship a tagged GitHub release and a wheel/sdist on PyPI after confirming package-name
  ownership. Use trusted publishing when available; keep runtime dependencies at zero.
  Version suggestions are a patch for these fixes and a later beta for the sharing and
  coverage contract, not a promise that either release has happened.
- Make the README's first screen answer one question and show one useful screenshot.
  Provide a synthetic demo, a short walkthrough, and a three-command install/list/serve
  path. Document that `latest` spans harnesses; an explicit session ID/path is safer.
- Publish an adapter capability matrix: usage coverage, context size, tool timing,
  reasoning visibility, cost basis, and known CLI-version gaps. List prices are not
  subscription invoices; estimated latency is not provider-side performance telemetry.
- Add contribution instructions, a changelog, a private security-reporting route, and a
  bug template that requests version/diagnostic metadata rather than transcripts.
- Invite roughly 10 developers across the three harnesses. Ask them to analyze their own
  sessions locally and report the decision the panel helped them make. Do not collect
  transcripts or add telemetry by default.

**Proposed validation gates, not forecasts:** 8 of 10 can reach a useful panel within
five minutes without help; 5 return for a second session within two weeks; 3 identify a
concrete change to their workflow. Gather this through voluntary follow-up. Failure is
feedback to simplify onboarding or sharpen the use case, not a reason to add more charts.

## Milestone 3 — turn observations into decisions

**Dependency: beta feedback identifies recurring questions.**

Build an offline, deterministic `advise` command over the shared analysis payload. Start
with three explainable rules: unusually large retained tool traffic, expensive cache
rebuild exposure, and repeatedly costly solo tool-schema searches. Each finding needs:

- the observation and request/lap references;
- what is measured versus inferred, and when the rule is inapplicable;
- one suggested action and its trade-off;
- a defensible estimate/range, or no dollar claim when prices/coverage are unknown.

Do not automatically compact, rewrite prompts, modify tool settings, or call another
model. Compaction can lose information; a cheaper session can also solve the task worse.

Add a focused comparison view for before/after sessions: separate cache reads from
computed work, label harness/model changes, and let users record a task outcome. Do not
imply that different tasks are a controlled experiment. Optional terminal hooks should
render these same findings instead of implementing a second accounting engine.

**Exit gate:** advice has synthetic oracle tests and stable JSON output; beta users can
follow an explanation back to evidence; several voluntarily report a useful change.
Savings and quality claims require outcome-aware comparisons, not token reduction alone.

## Milestone 4 — grow only where users pull

Choose one direction based on evidence, not all of them:

- **Harness builders:** publish a versioned adapter contract and local benchmark harness;
  add server-log timing for a local inference runtime if users need measured prefill/decode.
- **Power users/teams:** local SQLite indexing and incremental analysis for repository/day
  summaries and fast fleet navigation. Reconcile resume/fork lineage before aggregating
  costs, or shared history will be counted more than once. Keep raw text out of indexes
  unless explicitly needed and approved.
- **Broader harness support:** add the most-requested adapter against the common contract
  and synthetic fixtures, not another format-specific UI.

As touched areas grow, extract adapters, accounting, and presentation/export modules while
preserving CLI behavior and versioning the JSON contract. Do not make a wholesale rewrite
or a plugin framework a prerequisite for the beta. Set benchmark budgets on a declared
machine and synthetic workloads; optimize what actually exceeds them.

**Exit gate:** the next investment solves a repeated beta problem, has a named regression
or performance target, and leaves existing fixtures and public commands intact.

## What I would not build yet

A hosted transcript service, account system, billing integrations, paid dashboard,
automatic prompt optimizer, graph-memory engine, or a long list of new adapters. They
multiply privacy/support obligations before demand is established. The graph export can
remain an advanced feature without becoming the front door.

If repeated team use later reveals a paid problem, optional local/team reporting, support,
or integrations are plausible. Willingness to pay is untested; open-source adoption is
not evidence of a viable hosted business.

## Three implementation slices when distribution becomes a goal

1. License/attribution decision, tracked-build cleanup, CI, and installed-package tests.
2. Usage/capability disclosure with matching throughput coverage and partial-cost labeling.
3. A single sharing-safe payload transform, used by every exporter, with seeded-leak tests.

These are independently reviewable changes. After them, invite the small beta before
committing to milestone 3. The larger research ideas in `field-notes.md` remain useful,
but safe distribution should now take priority over precision features that need API keys.
