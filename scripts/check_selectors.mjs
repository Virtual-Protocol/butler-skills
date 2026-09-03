#!/usr/bin/env node
// check_selectors.mjs — recompute a 4-byte function selector from a Solidity
// signature using viem's keccak256/toFunctionSelector. Installs nothing at
// runtime: it expects `viem` to already be resolvable (either hoisted into
// node_modules next to this script for CI, or via NODE_PATH pointing at the
// container's ~/.openclaw/skills/acp-cli/node_modules/viem).
//
// Usage:
//   node scripts/check_selectors.mjs --signature "approve(address,uint256)"
//     -> prints the recomputed selector, e.g. 0x095ea7b3
//
//   node scripts/check_selectors.mjs --signature "approve(address,uint256)" --expect 0x095ea7b3
//     -> exits 0 if it matches, exits 1 and prints a mismatch message otherwise
//
// --help prints this usage block.

import { toFunctionSelector } from "viem";

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--help" || a === "-h") {
      out.help = true;
    } else if (a === "--signature") {
      out.signature = argv[++i];
    } else if (a === "--expect") {
      out.expect = argv[++i];
    }
  }
  return out;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help || !args.signature) {
    console.log(
      'Usage: node scripts/check_selectors.mjs --signature "approve(address,uint256)" [--expect 0x095ea7b3]'
    );
    process.exit(args.help ? 0 : 1);
  }

  let selector;
  try {
    selector = toFunctionSelector(args.signature);
  } catch (err) {
    console.error(`could not compute selector for ${JSON.stringify(args.signature)}: ${err.message}`);
    process.exit(1);
  }

  if (args.expect) {
    if (selector.toLowerCase() !== args.expect.toLowerCase()) {
      console.error(`mismatch: signature ${args.signature} -> ${selector}, expected ${args.expect}`);
      process.exit(1);
    }
    console.log(selector);
    process.exit(0);
  }

  console.log(selector);
  process.exit(0);
}

main();
