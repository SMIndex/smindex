"""Recompute the REPLAY-6M-v1.2 summary table from docs/models/replay-v1.2-takes.csv (stdlib only).
takes    = rows with a trade (exit_reason not starting with 'no_trade')
filled   = takes with entry_fill_ts set (cancelled:* rows have none)
win rate = filled rows with r_multiple > 0 / filled
exp R    = mean r_multiple over filled rows;  sum R = sum;  PF = sum(R>0) / -sum(R<0)
usage: python summary_from_csv.py docs/models/replay-v1.2-takes.csv"""
import csv, sys
from collections import defaultdict
g = defaultdict(list)
for r in csv.DictReader(open(sys.argv[1], encoding="utf-8")):
    g[(r["fill_model"], r["model"])].append(r)
print(f"{'fill':8}{'model':6}{'fired':>6}{'takes':>6}{'filled':>7}{'wins':>5}{'win%':>7}{'expR':>8}{'sumR':>8}{'PF':>6}  exit reasons")
for (fill, model), rows in sorted(g.items()):
    takes = [r for r in rows if not r["exit_reason"].startswith("no_trade")]
    filled = [r for r in takes if r["entry_fill_ts"]]
    R = [float(r["r_multiple"]) for r in filled if r["r_multiple"] != ""]
    wins = [x for x in R if x > 0]; losses = [x for x in R if x < 0]
    pf = (sum(wins) / -sum(losses)) if losses else float("inf")
    reasons = defaultdict(int)
    for r in takes: reasons[r["exit_reason"]] += 1
    print(f"{fill:8}{model:6}{len(rows):6}{len(takes):6}{len(filled):7}{len(wins):5}"
          f"{(100*len(wins)/len(R) if R else 0):7.1f}{(sum(R)/len(R) if R else 0):8.3f}{sum(R):8.2f}{pf:6.2f}  {dict(reasons)}")
