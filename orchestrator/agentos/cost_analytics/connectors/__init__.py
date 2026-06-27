"""Phase-2 live connectors — swap the input adapter without changing record shape.

A connector pulls cost from a live source (GCP BigQuery billing export, Stripe, …) and
produces the SAME CostRecord dicts the seeded-CSV loaders emit, then writes them via the
store. The MVP loaders read fixtures; connectors here read real data behind the same
contract (see docs/cost-tracking-plan.md "Phase 2").
"""
