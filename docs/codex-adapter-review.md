# Codex adapter review — 2026-09-06

Reviewed the adapter at `fd81dbb` together with the existing uncommitted adapter
improvements. The implementation is a useful foundation: it shares the downstream
analysis model, pairs tools by call ID, excludes encrypted reasoning content, and
preserves activity whose usage has not been confirmed. The existing working tree
passed 44 tests before this review.

## Corrections

Two synthetic regressions exposed problems in provisional usage reconciliation:

- A matching `last_token_usage` with unchanged cumulative totals incorrectly
  confirmed an early usage record. The regression reported 3,900 tokens instead
  of 2,550. Confirmation now requires changed cumulative totals.
- A corrected final counter could lose the request's usage because the early
  record had already claimed its model call. The regression reported 1,350 tokens
  instead of 2,550. The final counter now replaces provisional usage within the
  same request, retaining the existing response-tail merge.

Provisional reconciliation is bounded by turn-context, compaction, interruption,
and new user/developer input boundaries. Unconfirmed activity remains visible
without contributing usage to accounting.

## Validation and limits

- All 46 unittest tests pass; `git diff --check` passes.
- A local survey of 40 recent Codex rollouts produced no parse exceptions,
  non-additive time splits, or ledger-bound violations. Twelve had confirmed
  detailed usage. These structural checks do not establish the accuracy of every
  inferred phase or ledger attribution.
- Several migrated legacy records retain `total_tokens` while detailed input,
  output, and reasoning counters are zero. Their accounting cannot be reconstructed
  reliably from those fields; zero confirmed detailed usage is not proof of zero
  historical consumption.
- The current Astra conversation was served locally and inspected in a browser.
  Its model identity is correct; dollar cost is omitted for the unpriced model.
- No real transcript or generated real-session panel is included in this report.

Pricing was not refreshed in this review. Timing phases and context-category token
counts remain estimates; usage confirmation does not improve their precision.
