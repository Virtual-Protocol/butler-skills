---
name: bevo-contract-call
description: Build, dry-run and file any named contract call (approve/deposit/stake/claim) as one approval transaction. Use for "call/approve/interact with <contract>".
version: 1.0.0
metadata: {"openclaw":{"emoji":"🔗","requires":{"bins":["acp","bevo-read","bevo-rpc","node"]}},"bevo":{"tier":"on-demand","modes":["one-off","duty"],"moneyMoving":true,"keywords":["contract call","approve","deposit","stake","claim rewards"],"requires":{"routes":["GET /butler-read/me","GET /butler-read/assets","POST /butler-exec/execute","GET /butler-read/exec-requests","POST /butler-exec/services"],"features":["executeIdempotency","execRequestStatus"],"gates":[],"bins":["acp","bevo-read","bevo-rpc","node"]},"params":[{"name":"CHAIN_ID","type":"chainId","default":8453},{"name":"CONTRACT","type":"address","required":true,"ask":"which contract?"},{"name":"FUNCTION","type":"string","required":true,"ask":"which function, as a Solidity signature, e.g. approve(address,uint256)?"},{"name":"ARGS","type":"string","required":true,"ask":"what arguments, as a JSON array?"},{"name":"VALUE_WEI","type":"int","default":0},{"name":"SELECTOR","type":"string","required":false,"help":"the 4-byte selector for FUNCTION, precomputed once via the node encodeFunctionData recipe (step 3) — only needed for the duty variant, since duty.py has no keccak"}],"web3":{"chains":[8453],"contracts":[{"name":"USDC","chainId":8453,"address":"0x833589fCD6eDb6e08f4c7C32D4f71b54bdA02913","functions":[{"signature":"approve(address,uint256)","selector":"0x095ea7b3"}]}]},"dutyTemplate":"duty.py"}}
---

## When to use

The owner names a specific contract and function (approve, deposit, stake, add liquidity,
claim rewards) rather than a spot/perp trade. This is the generic pattern every DeFi/LP/
staking/approval skill copies. Never for a plain spot swap (use `acp trade`) or a send
(use `bevo-send`).

## Before you start

Echo the contract, chain, function and arguments back to the owner before doing anything —
transcription errors are the top failure mode. Resolve the agent wallet address (the `from`
for every dry run):

```bash
bevo-read me
```

Read any precondition the function implies — an existing allowance for a spender argument,
or a balance for an amount argument — with `bevo-read assets` or an `eth_call`. Never write
on assumption.

## Customize

- `CHAIN_ID` (default 8453) — which chain the contract lives on.
- `CONTRACT` (required, asked as "which contract?") — the target address.
- `FUNCTION` (required, asked as "which function, as a Solidity signature?") — e.g.
  `approve(address,uint256)`.
- `ARGS` (required, asked as "what arguments, as a JSON array?") — positional arguments in
  declaration order.
- `VALUE_WEI` (default 0) — wei to send with the call (0 for a plain approve/claim).

`[ADAPT]` steps: how the owner phrased the intent, which precondition reads apply, whether a
prior approval leg is needed first. `[FIXED]` steps: the read-build-dry-run-file sequence,
the `eth_getCode` wrong-chain check, the idempotency key, waiting on a prior leg.

## Contracts

| Contract | Chain | Function | Selector |
| --- | --- | --- | --- |
| USDC (0x833589fCD6eDb6e08f4c7C32D4f71b54bdA02913) | 8453 | `approve(address,uint256)` | `0x095ea7b3` |

## One-off procedure

1. [FIXED] Confirm the target is a contract, not an EOA, before building anything:

   ```bash
   bevo-rpc CHAIN_ID eth_getCode '["CONTRACT", "latest"]'
   ```

   If the result is `0x`, stop and tell the owner the address looks like a wallet, not a
   contract.
2. [FIXED] Read any precondition state first (allowance, reserves, position) — never write
   on assumption:

   ```bash
   bevo-rpc CHAIN_ID eth_call '[{"to":"CONTRACT","data":"0x..."}, "latest"]'
   ```

3. [FIXED] Build the calldata with the viem one-liner (selector + zero-padded 32-byte words):

   ```bash
   node -e "console.log(require('viem').encodeFunctionData({abi: require('viem').parseAbi(['function FUNCTION']), functionName: 'FUNCTION'.split('(')[0], args: ARGS}))"
   ```

4. [FIXED] Dry-run the exact calldata from the agent wallet to catch a revert for free:

   ```bash
   bevo-rpc CHAIN_ID eth_call '[{"from":"<agentWalletAddress>","to":"CONTRACT","data":"<calldata>"}, "latest"]'
   ```

   On a revert, report the revert reason and stop — never "fix" the arguments by guessing.
5. [FIXED] File it with one key for this leg:

   ```bash
   acp wallet send-transaction --chain-id CHAIN_ID --to CONTRACT --data <calldata> --idempotency-key contractcall:chat:<sha256(chain|to|data|value)[:16]>
   ```

6. [FIXED] Report the `approvalId` and describe in plain words what the card will do (the
   card only shows contract + selector for a raw `other` calldata).
7. [ADAPT] If this is the first of two legs (e.g. approve then deposit), wait for the first
   leg's approval outcome before filing the second:

   ```bash
   bevo-read request <key1> --route execute
   ```

   Never file both legs blind.

## Duty procedure

A `timer` trigger fits recurring calls (rebalance/claim/compound every N hours). `duty.py`
has no keccak, so bake the `SELECTOR` constant once, at creation time, with the same node
recipe from step 3 above — never guess it.

1. [FIXED] Trigger JSON: `{"kind":"timer","intervalSeconds":<n>}` (or `dailyAt` + `timezone`).
2. [FIXED] Compute `SELECTOR` once with the node recipe (step 3 of the one-off procedure)
   and set `env` = the six params above, including it.
3. [FIXED] Create it:

   ```bash
   bevo-automation create --from-skill bevo-contract-call@1.0.0 '<json>'
   ```

4. [FIXED] Say to the owner: "Created, pending — arm it in Approvals; each tick files one
   approval card for this call; nothing runs until you arm a pocket."

## Idempotency and retries

Key formula: `contractcall:chat:<sha256(chain|to|data|value)[:16]>` for a one-off,
`contractcall:{SERVICE_ID}:<tick_iso>` per duty tick — one key per leg, never shared across
legs of a multi-step flow.

- `accepted` / an `approvalId` is returned — the leg is filed; do not re-run it.
- Network error before a response — do not re-run; check
  `bevo-read request <key> --route execute` first.
- `IDEMPOTENT_IN_FLIGHT` — wait, do not resend.
- `IDEMPOTENCY_KEY_REUSED` — a different call already used this key; log it, do not file.
- `IDEMPOTENT_UNKNOWN_OUTCOME` — log it and do not re-file the leg; this is final.

## Failure handling

| Outcome | What to do |
| --- | --- |
| `approvalId` returned | Report it; explain what the card will do. |
| Rejected in Approvals | Stop the whole flow; tell the owner. |
| Revert in dry run | Report the revert reason; stop; do not guess new arguments. |
| `IDEMPOTENT_UNKNOWN_OUTCOME` | Log; never re-file the leg. |
| `eth_getCode` returns `0x` | Stop before building anything; the address is not a contract. |

## Limits

Always files an approval card — Butler never signs or broadcasts. No packed calldata
(4-byte selector + 32-byte words only), ~49 KB max. `to` is always required (no
deployments). A wrong-chain address is caught by `eth_getCode` before anything is filed.

## Say to the owner

"Built the call to `<function>` on `<contract>` (chain `<chainId>`) and filed it — approve
`<approvalId>` in Approvals to send it."
