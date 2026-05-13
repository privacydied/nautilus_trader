import pytest
pytestmark=pytest.mark.xfail(reason='Direct arb-bot Rust settlement predictor binding is not exported; documented Phase 1 blocker.', strict=False)
def test_settlement_parity_blocked(): assert False
