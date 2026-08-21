# XA05 MOM12-7 plus frozen P00 final transfer

XA05 is the final system-assessment round for the current research line. XA04 mechanically selected no tree, so the only base alpha is the same-universe raw `XS003_MOM_12_7`. XA05 does not train a model or search a policy.

For weekly and monthly Top 5/10/20/50, it compares the naked alpha, the unchanged weekly P00 overlay, and a path-specific static allocation matched to P00's zero-cost mean realized stock exposure. The union-event engine reranks stocks only on the base strategy's own rebalance dates; overlay-only dates preserve relative holdings and change total exposure.

The result package emphasizes both return and drawdown anatomy. It includes daily NAV and exposure in runtime, compact metrics and worst drawdown episodes in Git, and final charts for NAV, underwater depth, rolling drawdown, recovery duration, annual returns, state exposure, and cross-cell robustness.
