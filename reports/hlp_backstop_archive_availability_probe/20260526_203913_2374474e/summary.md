# HLP Backstop Archive Availability Probe — 20260524

**Run ID:** 20260526_203913_2374474e
**Timestamp (UTC):** 2026-05-26T20:39:13Z
**Git SHA:** 1984ef5541a2234571d884ca206de81cd4de1d3c
**Branch:** feat/hlp-backstop-absorption-phase0a-star-coverage

## S3 Bucket Summary

- **Bucket:** `hl-mainnet-node-data`
- **Prefixes available:** 6

## node_fills_by_block/hourly Archive
- **Dates:** ? → ? (0 dates)
- **Files in date:** 24, Total: 602.5 MiB
- **Sample:** 26.7 MiB compressed, 51946 blocks
- **Verdict:** `NODE_FILLS_BY_BLOCK_SCHEMA_READY`

## misc_events_by_block/hourly Archive
- **Verdict:** `MISC_EVENTS_SCHEMA_READY_NO_LIQUIDATION_MARKERS`
- **Event types:** CWithdrawal, Delegation, Funding, GossipPriorityAuctionRestart, LedgerUpdate, ValidatorRewards

## explorer_blocks Archive
- **Format:** rmp_msgpack
- **Sample:** 2.7 MiB compressed, 100 blocks, 136342 actions
- **Action types:** ['order', 'cancelByCloid', 'cancel', 'batchModify', 'modify', 'noop', 'scheduleCancel', 'updateLeverage', 'evmRawTx', 'perpDeploy']
- **Has NetChildVaultPositionsAction:** True
- **Vault addresses found:** 2
- **Liquidation markers found:** ['liq', 'margin', 'vault']
- **Verdict:** `EXPLORER_BLOCKS_SCHEMA_READY_WITH_LIQUIDATION_MARKERS`

## replica_cmds Archive
- **Sub-prefixes:** 71
- **Date 20260524 exists:** False
- **Verdict:** `REPLICA_CMDS_SCHEMA_READY_NO_LIQUIDATION_MARKERS`
- **Note:** Files are 700 MiB-2 GiB each — not listed or downloaded. Skipping full enumeration to stay within bounded probe limits.

## Vault Details Probe
- **Verdict:** `VAULT_DETAILS_NO_CHILD_ROLES`
- **Parent resolved:** False
- **Child vaults found:** False
- **Child roles present:** False
- **Backstop role found:** False

## Liquidation Marker Search Results
- **explorer_blocks:** terms found: ['vault', 'margin', 'liq']
  Join keys: ['block', 'time', 'user', 'hash']

## Combined Verdicts

- **node_fills_by_block:** `NODE_FILLS_BY_BLOCK_SCHEMA_READY`
- **misc_events_by_block:** `MISC_EVENTS_SCHEMA_READY_NO_LIQUIDATION_MARKERS`
- **explorer_blocks:** `EXPLORER_BLOCKS_SCHEMA_READY_WITH_LIQUIDATION_MARKERS`
- **replica_cmds:** `REPLICA_CMDS_SCHEMA_READY_NO_LIQUIDATION_MARKERS`
- **vault_details:** `VAULT_DETAILS_NO_CHILD_ROLES`

## Liquidation Join Assessment

No liquidation/backstop marker found in any sampled archive source.
`NetChildVaultPositionsAction` in explorer_blocks provides vault address
resolution (child vaults). This enables address-based filtering in fills
but does not directly mark fills as backstop/liquidation-related.

## Safety

- No orders, auth, trading, live, paper, shadow, bot, systemd, or registry mutation.

