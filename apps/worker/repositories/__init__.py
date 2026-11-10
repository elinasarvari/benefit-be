"""MongoDB access for the worker, called only from activities (research R4, TW-2).

Every query on a household-scoped collection goes through `packages.platform.scope` with the case's
system scope, plus the household ID (constitution VIII).
"""
