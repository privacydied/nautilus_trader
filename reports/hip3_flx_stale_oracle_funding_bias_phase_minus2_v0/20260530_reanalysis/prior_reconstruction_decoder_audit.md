# Prior Reconstruction Decoder Audit

## Date: 2026-05-30

## Branch audited
`feat/hip3-replica-cmds-setoracle-reconstruction-v0`

## File audited
`examples/strategies/venue_agnostic_signal_observer/hip3_replica_cmds_setoracle_reconstruction_v0.py`

## Finding

The prior reconstruction decoder has **the same bug** as the current scanner prior to the fix.

### Bug location

Line 1642 in the prior decoder:

```python
if action.get("type") != "perpDeploy":
    continue
```

### Root cause

The prior decoder traverses:
```
abci_block -> signed_action_bundles[i] -> signed_actions[j] -> action -> type
```

It checks if `action.type == "perpDeploy"` and skips if not. But actual `replica_cmds` data wraps the deployer oracle update inside a multiSig envelope:

```
signed_actions[j] -> action(type=multiSig) -> payload -> action(type=perpDeploy) -> setOracle -> oraclePxs
```

Since `action.type` is `"multiSig"`, not `"perpDeploy"`, the decoder silently skips every oracle update. All `flx:*` keys were dropped.

### Impact on prior findings

The prior reconstruction reported:
- `flx_seen_count: 0`
- `flx_absent_while_other_dexes_active: true`
- `dexes_seen: ["cash", "km", "para"]`

These findings are **invalidated** as extraction false negatives. The `flx` namespace was not absent from the archive — the decoder simply could not reach it.

### Why cash/km/para were found but flx was not

Some `cash`, `km`, and `para` oracle updates use the direct `action.type = "perpDeploy"` path (not wrapped in multiSig), so they were detected. The `flx` updates all use the multiSig envelope, so they were all dropped.

### Corrected results (3-date reanalysis)

| DEX | TSLA count | NVDA count | updates/hour |
|-----|-----------|-----------|-------------|
| flx | 646 | 646 | 16.18 |
| cash | 1121 | 1121 | 28.07 |
| km | 461 | 461 | ~11.5 |
| xyz | 552 | 552 | ~13.8 |

### Conclusion

The stale/sparse `flx` oracle premise was a **decoder artifact**, not an empirical finding. B-slow's stale-oracle foundation is not supported by corrected oracle-frequency evidence.
