"""Shared feature layer (doc 00 §4). Pure computation (indicators, timing,
sessions) is unit-tested against known values; the data-backed features
(regime, funding_gauge, bias) read real rows from the strat_ tables and the
existing analytics tables. Nothing here places an order."""
