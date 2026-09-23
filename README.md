# Concord
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/license/mit/)
[![Discord](https://img.shields.io/badge/Discord-Join%20us-5865F2?logo=discord&logoColor=white)](https://discord.gg/8Jm4v89VAu)
[![Telegram](https://img.shields.io/badge/Telegram--T.svg?style=social&logo=telegram)](https://t.me/genlayer)
[![Twitter](https://img.shields.io/twitter/url/https/twitter.com/yeagerai.svg?style=social&label=Follow%20%40GenLayer)](https://x.com/GenLayer)

## About
Concord is a generic **N-of-M source equivalence oracle** - a GenLayer
Intelligent Contract with no LLM anywhere in it. Validators independently
fetch every source registered against a query, extract one comparable value
from each via a caller-supplied JSON path, and reach consensus only when
enough of them genuinely agree.

A single-purpose price checker hardcodes its comparison into the contract.
Concord makes the source list, extraction path, tolerance, and agreement
threshold all caller-supplied parameters set at query creation time -
reusable infrastructure any GenLayer dapp can call for "verify a fact
across independent sources" instead of rebuilding the pattern from scratch.

`create_query(query_id, sources, json_path, tolerance_bps, threshold_count)`
registers 2-8 source URLs, a dot/bracket path to pull one leaf value out of
each source's JSON response (e.g. `"data.price.usd"` or
`"results[0].value"`), a tolerance in basis points for numeric agreement
(`0` = exact match), and how many sources must agree.

`resolve(query_id)` fetches every source, extracts a value from each, and
clusters them:
- **Numeric values** are compared via integer-scaled fixed-point
  arithmetic (`value * 1e8`, rounded) - no ambiguity about how "close
  enough" is computed, and every comparison is a plain integer operation.
- **Non-numeric values** are compared by exact string match.

The largest agreeing cluster wins if it meets `threshold_count`. Validators
independently recompute the identical resolved value and agreeing count and
must match byte-for-byte before consensus is reached via the equivalence
principle - agreement itself is the entire point of this primitive, so both
fields are consensus-critical, not an informational byproduct.

A source that's unreachable, times out, or returns something the JSON path
can't resolve is simply excluded from consideration, not counted as
disagreement. Anyone can independently re-fetch the same sources
(`get_sources`) and recompute the same result themselves from the public
data alone - nothing here needs to be trusted, only checked. No value ever
moves through this contract.

## Deployment
Deployed on **GenLayer Bradbury Testnet** (chain ID 4221):
- **Contract:** [`<pending>`](https://explorer-bradbury.genlayer.com)
- Verified via 22 passing direct-mode tests (`python -m pytest tests/direct/`),
  covering query creation and its full validation surface (source count
  bounds, non-`https://` rejection, tolerance/threshold range checks),
  full and partial numeric agreement, below-threshold reverts, dead/malformed
  source exclusion, a numeric tolerance boundary case, exact-match mode for
  both numeric and string values, nested array JSON paths, double-resolve
  rejection, and independent queries.

## What's included
- `contracts/concord.py` — the Concord Intelligent Contract
- `tests/direct/test_concord.py` — direct-mode tests (in-memory, mocked HTTP sources)
- **Contract linting** — static analysis to catch common contract issues before deployment
- **CI pipeline** — GitHub Actions workflow for linting and direct tests
- Configuration file template and a deployment script

This is a contract-only primitive, deliberately without a frontend - the
point is to be called by other contracts and integrators, not to be a
product on its own.

## Requirements
- Python >= 3.12
- [GenLayer CLI](https://github.com/genlayerlabs/genlayer-cli) globally installed: `npm install -g genlayer`
- GenLayer Studio (for integration tests and deployment): Install from [Docs](https://docs.genlayer.com/developers/intelligent-contracts/tooling-setup#using-the-genlayer-studio) or use the hosted [GenLayer Studio](https://studio.genlayer.com/)

## Project Structure

```
contracts/              # Python intelligent contracts
  concord.py               # Concord
tests/
  direct/                # Fast in-memory tests (no Studio required)
    test_concord.py
deploy/                  # TypeScript deployment scripts
gltest.config.yaml       # Test runner network configuration
pyproject.toml           # Python/pytest configuration
.github/workflows/       # CI pipeline
```

## Quick Start

### 1. Set up Python environment

```shell
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Lint the contract

```shell
genvm-lint check contracts/concord.py
```

### 3. Run direct mode tests

```shell
python -m pytest tests/direct/ -v
```

Use `python -m pytest`, not bare `pytest` - depending on your installed
pytest version, running the bare command can fail to put the project
root on `sys.path`, breaking test discovery with
`ModuleNotFoundError: No module named 'tests'`.

### 4. Deploy the contract

1. Choose your network: `genlayer network`
2. Deploy: `genlayer deploy` (runs the script in `/deploy/deployScript.ts`)

## How Concord Works

1. **`create_query(query_id, sources, json_path, tolerance_bps, threshold_count)`**
   — registers up to 8 source URLs, the extraction path, the numeric
   tolerance, and how many sources must agree. Reverts on a duplicate id,
   an out-of-range source count, a non-`https://` source, an empty
   `json_path`, or out-of-range tolerance/threshold values.
2. **`resolve(query_id)`** — fetches every source, extracts a value from
   each via `json_path`, clusters the results, and records the largest
   agreeing cluster if it meets `threshold_count`. Reverts with a clean
   "Quorum not reached" if not.
3. **`get_query(query_id)`** / **`get_sources(query_id)`** — read back a
   query's full state and its registered source list.

### Example: a 3-of-5 price check

```python
concord.create_query(
    "eth-price-check",
    [
        "https://api.coinbase.com/v2/prices/ETH-USD/spot",
        "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd",
        "https://api.kraken.com/0/public/Ticker?pair=ETHUSD",
        "https://api.bitfinex.com/v1/pubticker/ethusd",
        "https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT",
    ],
    json_path="data.amount",   # different per source in practice - one query per source shape
    tolerance_bps=50,           # 0.5%
    threshold_count=3,
)
concord.resolve("eth-price-check")
```

## Testing Strategy

| Test Type | Command | Speed | Requires Studio |
|-----------|---------|-------|-----------------|
| **Lint** | `genvm-lint check contracts/concord.py` | ~250ms | No |
| **Direct** | `python -m pytest tests/direct/ -v` | ~ms/test | No |

## Community
- **[Discord](https://discord.gg/8Jm4v89VAu)**: Discussions, support, and announcements
- **[Telegram](https://t.me/genlayer)**: Informal chats and quick updates

## Documentation
For detailed information, see our [documentation](https://docs.genlayer.com/).

## License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
