"""Strategy implementations (docs 01–06), driven by the evaluation framework in
base.py. Each logs every evaluation to strat_signals, emits doc-format Telegram
outbox rows, exposes its conditions in evaluation order, and (in paper) routes
fired intents through RiskEngine → PaperExecutor. History-dependent conditions
render 'not enough history yet' and count as NOT EVALUATED — never a fail."""
