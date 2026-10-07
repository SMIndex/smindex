"""Event-driven backtesters (Part E). Replay from the strat_ tables using the SAME
strategy classes and the SAME PaperExecutor. Where a required series does not
exist for the requested range (OI-1m / trades / book / liquidations before Phase 1
went live), the report says 'not backtestable yet — coverage begins <ts>' instead
of computing numbers from partial inputs."""
