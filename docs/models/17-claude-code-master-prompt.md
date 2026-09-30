# 17. Master Prompt for Claude Code: Implement M1 to M6 and Start Paper Trading

Give Claude Code the eight files in this folder (10 to 16 and this one). Then paste the prompt below exactly.

```
You are working in the Perpl Terminal repository where strategies 01 to 06 already run in paper mode inside the strategy engine. Implement everything described in the attached docs 10-structure-engine-and-mind-framework.md, 11-M1-sweep-and-reclaim.md, 12-M2-break-of-structure-order-block.md, 13-M3-failed-auction-range-extreme.md, 14-M4-htf-change-of-character.md, 15-M5-session-liquidity-run.md and 16-M6-weekly-open-reclaim.md, in one continuous run, without stopping to ask me for permission, confirmation or preferences at any point. Where a doc leaves a detail open, choose the simplest option consistent with the doc, record the choice in docs/models/DECISIONS.md, and continue. Do not modify the behaviour of strategies 01 to 06, the RiskEngine limits, or the live execution path.

Order of work:

1. Read all seven docs fully before writing code.

2. Implement the shared layer from doc 10 exactly: the structure/ package (swings, trend, BOS, CHoCH, displacement, order blocks, FVGs, liquidity pools including liquidation clusters from the existing liquidation map, 4h and 1h ranges with premium and discount, sweeps, session VWAP with bands, volume profile, the alignment table, the day type classifier) and the mind/ package (Reason, Veto, ContextRule, InTradeCheck, Decision, Mind with evaluate, manage and thesis, Snapshot builder from the existing data layer, weekly learn job with calibration report, disabled until 60 days after each model's first trade). Extend the signals and trades tables with the columns listed in doc 10 section 5 and create the mind_weights and mind_calibration tables. Add config/models.yaml with the common block. Write and run the unit tests listed in doc 10 and fix until green.

3. Implement the six strategies exactly as specified in docs 11 to 16, each as its own class with its own Mind (reasons, weights, vetoes, context multipliers, in-trade checks, thesis template), each tagged with its model id, each for BTC and ETH, each in paper mode using the existing paper executor with post-only fills modelled as filled only when a trade prints through the limit price (spec v1.3, D-87: a post-only order that would cross the touch is rejected and re-quoted once one tick inside, then rests; D-88: 0.5 ATR minimum stop distance applied centrally). Write and run the unit tests listed at the end of each doc and fix until green.

4. Register M1 to M6 in the strategy scheduler alongside 01 to 06: evaluate on each closed 15m candle (M4 also on each closed 1h candle), run Mind.manage on each closed 15m candle for open positions, run the day type classifier at 07:00 and 14:00 UTC and as a running label, freeze the Asia range at 07:00 UTC and the weekly levels at Monday 00:00 UTC, and schedule mind/learn.py weekly on Sunday 00:30 UTC. All six start with mode paper in config/models.yaml and are enabled.

5. Add M1 to M6 to the Strategies page: rows in the overview table with a "Model" badge, detail pages using the existing template where the conditions panel shows one row per Mind reason (name, strength bar, weight, contribution), a vetoes row, a multipliers row, conviction with size tier, and the thesis text; a Breakdown tab showing the calibration table and weight history. Reuse the existing components and themes. Do not change the pages for 01 to 06.

6. Add Telegram alerts for M1 to M6 per each doc's alert list using the existing alert module, with the model id in every message, and per-model checkboxes in Settings.

7. Restart the server and the strategy engine process. Then verify, and fix anything that fails before continuing:
   a. The structure engine produces an alignment table for BTC and ETH with non-empty swings, a trend label, a 4h range with premium or discount, at least one pool above and below, a session label and a day type. Print one table per coin to the log.
   b. Each of M1 to M6 logs at least one evaluation row to the signals table within the first 30 minutes of running, even if every evaluation is a skip. If a model logs nothing, trace why (missing data, exception, or a condition that can never be true) and fix it.
   c. The Mind fields (reasons_json, vetoes_json, multipliers_json, conviction, size_tier, thesis) are populated on every logged evaluation.
   d. Paper orders are created when a Mind returns take, appear in the trades table with model tag, expected_hold_min and r_multiple on exit, and the in-trade checks are being evaluated on each 15m close for open positions.
   e. The Strategies page renders M1 to M6 rows and detail pages without console errors in both themes.
   f. A Telegram alert is delivered for at least one M1 to M6 evaluation.
   Run a 60-minute soak: keep the process running, tail the logs, and confirm no unhandled exceptions, no repeated reconnects, and no gaps in the alignment table updates. Fix and restart as needed until the soak passes.

8. If after 6 hours of running none of M1 to M6 has fired a paper trade, do not lower any threshold. Instead write docs/models/FIRST-6H-REPORT.md listing, per model and per coin, how many evaluations occurred, how many were vetoed and by which veto, and the distribution of conviction for non-vetoed evaluations. Include the current alignment table and day type. Vetoes and low conviction on quiet hours are expected behaviour, not a bug. Only treat it as a bug if a model has zero evaluations, if a veto fires on 100 percent of evaluations across different market states, or if a detector returns a constant value.

9. Write docs/models/IMPLEMENTATION-REPORT.md: files created and changed, how each model is scheduled, the verification results from step 7 with the printed alignment tables, the soak result, and any open issues. Commit with the message "Add structure engine, heuristic minds and models M1-M6 in paper mode".

Rules for the whole run: no phase gates, no approval stops, no "let me know if you want me to continue". Do not add any AI model calls; the Mind is deterministic code. Do not enable live mode for anything. Do not change risk limits. Model post-only fills conservatively. When something is ambiguous, decide, record it in DECISIONS.md, and keep going. Finish by confirming that the server is restarted, all twelve strategies (01 to 06 and M1 to M6) are running in paper mode, and M1 to M6 are logging evaluations.
```

## After it finishes

Read `docs/models/IMPLEMENTATION-REPORT.md` and `docs/models/DECISIONS.md`. Let all twelve run for at least two weeks. Then compare M1 to M6 against 01 to 06 on the Strategies page by signal count first, expectancy second. Nothing is judged before 30 signals.
