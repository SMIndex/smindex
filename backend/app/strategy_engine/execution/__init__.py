"""Execution adapters (doc 00 §6). This session ships exactly two:
`paper` (built here — simulates maker fills only when a trade prints THROUGH the
limit) and `null` (logs and drops). There is NO HL or Perpl execution adapter, so
`live` is impossible — venue is carried on every intent for Phase 3."""
