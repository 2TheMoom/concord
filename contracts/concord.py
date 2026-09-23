# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from genlayer import *

MIN_SOURCES = 2
MAX_SOURCES = 8
MAX_TOLERANCE_BPS = 10_000  # 100%
SCALE = 100_000_000  # fixed-point precision for numeric comparisons (1e8)

REQUEST_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


@allow_storage
@dataclass
class Query:
    creator: str
    created_at: u256
    json_path: str
    tolerance_bps: u256
    threshold_count: u256
    source_count: u256
    resolved: bool
    agreed_value: str  # "" until resolved
    agreeing_count: u256


class Concord(gl.Contract):
    """A generic N-of-M source equivalence oracle - validators independently
    fetch every registered source, extract one comparable value from each
    via a caller-supplied JSON path, and reach consensus only when enough
    of them genuinely agree.

    A single-purpose price checker hardcodes its comparison into the
    contract. Concord makes the source list, extraction path, tolerance,
    and agreement threshold all caller-supplied parameters set at query
    creation time - reusable infrastructure any GenLayer dapp can call for
    "verify a fact across independent sources" instead of rebuilding the
    pattern from scratch.

    create_query(query_id, sources, json_path, tolerance_bps,
    threshold_count) registers 2-8 source URLs, a dot/bracket path to pull
    one leaf value out of each source's JSON response (e.g. "data.price.usd"
    or "results[0].value"), a tolerance in basis points for numeric
    agreement (0 = exact match), and how many sources must agree.
    resolve(query_id) fetches every source, extracts a value from each, and
    clusters them: numeric values are compared via integer-scaled
    fixed-point arithmetic, non-numeric values by exact string match. The
    largest agreeing cluster wins if it meets threshold_count. Validators
    independently recompute the identical resolved value and agreeing
    count and must match byte-for-byte before consensus is reached -
    agreement itself is the entire point of this primitive, so both fields
    are consensus-critical, not an informational byproduct.

    A source that's unreachable, times out, or returns something the JSON
    path can't resolve is simply excluded from consideration, not counted
    as disagreement. Anyone can independently re-fetch the same sources
    (get_sources) and recompute the same result themselves - nothing here
    needs to be trusted, only checked. No value ever moves through this
    contract.
    """

    queries: TreeMap[str, Query]
    query_sources: TreeMap[str, DynArray[str]]

    def __init__(self):
        pass

    def _now(self) -> int:
        return int(datetime.now(timezone.utc).timestamp())

    @gl.public.write
    def create_query(
        self,
        query_id: str,
        sources: list[str],
        json_path: str,
        tolerance_bps: int,
        threshold_count: int,
    ) -> None:
        if query_id in self.queries:
            raise gl.vm.UserError(f"Query '{query_id}' already exists")
        if not (MIN_SOURCES <= len(sources) <= MAX_SOURCES):
            raise gl.vm.UserError(f"sources must have between {MIN_SOURCES} and {MAX_SOURCES} entries")
        for src in sources:
            if not src.startswith("https://"):
                raise gl.vm.UserError("every source must start with https://")
        if not json_path:
            raise gl.vm.UserError("json_path cannot be empty")
        if not (0 <= tolerance_bps <= MAX_TOLERANCE_BPS):
            raise gl.vm.UserError(f"tolerance_bps must be between 0 and {MAX_TOLERANCE_BPS}")
        if not (2 <= threshold_count <= len(sources)):
            raise gl.vm.UserError(f"threshold_count must be between 2 and {len(sources)}")

        self.queries[query_id] = Query(
            creator=gl.message.sender_address.as_hex,
            created_at=self._now(),
            json_path=json_path,
            tolerance_bps=tolerance_bps,
            threshold_count=threshold_count,
            source_count=len(sources),
            resolved=False,
            agreed_value="",
            agreeing_count=0,
        )
        for src in sources:
            self.query_sources.get_or_insert_default(query_id).append(src)

    def _extract_value(self, data, path: str):
        current = data
        for part in path.split("."):
            if not part:
                continue
            key = part
            indices = []
            while key.endswith("]") and "[" in key:
                base, idx_str = key.rsplit("[", 1)
                idx_str = idx_str[:-1]
                if not idx_str.isdigit():
                    return None
                indices.append(int(idx_str))
                key = base
            if key:
                if not isinstance(current, dict) or key not in current:
                    return None
                current = current[key]
            for idx in indices:
                if not isinstance(current, list) or idx < 0 or idx >= len(current):
                    return None
                current = current[idx]
        return current

    def _to_scaled(self, value):
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value * SCALE
        if isinstance(value, float):
            return round(value * SCALE)
        if isinstance(value, str):
            return self._parse_decimal_string(value)
        return None

    def _parse_decimal_string(self, s: str):
        """Some APIs quote numeric values as JSON strings (e.g.
        "price": "50123.45") to avoid float precision loss. Parse a plain
        (non-exponential) decimal string into the same scaled-int space as
        a bare numeric literal, so both encodings of the same fact can
        cluster together. Returns None for anything that isn't cleanly a
        plain decimal - those fall through to string exact-match instead."""
        if not s:
            return None
        neg = s.startswith("-")
        body = s[1:] if neg else s
        if "." in body:
            int_part, frac_part = body.split(".", 1)
        else:
            int_part, frac_part = body, ""
        if int_part == "" or not int_part.isdigit():
            return None
        if frac_part and not frac_part.isdigit():
            return None
        frac_part = (frac_part + "0" * 8)[:8]
        scaled = int(int_part) * SCALE + int(frac_part)
        return -scaled if neg else scaled

    def _fetch_and_extract(self, source: str, json_path: str):
        try:
            resp = gl.nondet.web.request(source, method="GET", headers=REQUEST_HEADERS)
            data = json.loads((resp.body or b"").decode("utf-8"))
        except Exception:
            return None
        leaf = self._extract_value(data, json_path)
        if leaf is None or isinstance(leaf, (dict, list)):
            return None
        return leaf

    def _resolve_logic(self, sources: list, json_path: str, tolerance_bps: int) -> dict:
        numeric_values = []
        string_values = []
        for src in sources:
            leaf = self._fetch_and_extract(src, json_path)
            if leaf is None:
                continue
            scaled = self._to_scaled(leaf)
            if scaled is not None:
                numeric_values.append(scaled)
            elif isinstance(leaf, str):
                string_values.append(leaf)

        best_size = 0
        best_value = ""

        if numeric_values:
            sorted_vals = sorted(numeric_values)
            n = len(sorted_vals)
            for i in range(n):
                anchor = sorted_vals[i]
                cluster = [
                    w
                    for w in sorted_vals
                    if abs(anchor - w) * 10000 <= tolerance_bps * max(abs(anchor), abs(w), 1)
                ]
                if len(cluster) > best_size:
                    best_size = len(cluster)
                    m = len(cluster)
                    best_value = str(cluster[(m - 1) // 2])

        if string_values:
            counts: dict = {}
            for s in string_values:
                counts[s] = counts.get(s, 0) + 1
            for s in sorted(counts):
                if counts[s] > best_size:
                    best_size = counts[s]
                    best_value = s

        return {"agreed_value": best_value, "agreeing_count": best_size}

    @gl.public.write
    def resolve(self, query_id: str) -> None:
        if query_id not in self.queries:
            raise gl.vm.UserError(f"Query '{query_id}' not found")
        q = self.queries[query_id]
        if q.resolved:
            raise gl.vm.UserError("This query has already been resolved")

        sources = list(self.query_sources.get(query_id, []))
        json_path = q.json_path
        tolerance_bps = int(q.tolerance_bps)
        threshold_count = int(q.threshold_count)

        def leader_fn() -> dict:
            return self._resolve_logic(sources, json_path, tolerance_bps)

        def validator_fn(leaders_res) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return False
            mine = leader_fn()
            return (
                mine["agreed_value"] == leaders_res.calldata["agreed_value"]
                and mine["agreeing_count"] == leaders_res.calldata["agreeing_count"]
            )

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        if result["agreeing_count"] < threshold_count:
            raise gl.vm.UserError(
                f"Quorum not reached: {result['agreeing_count']} of {q.source_count}, need {threshold_count}"
            )

        q.resolved = True
        q.agreed_value = result["agreed_value"]
        q.agreeing_count = result["agreeing_count"]

    @gl.public.view
    def get_query(self, query_id: str) -> dict:
        if query_id not in self.queries:
            raise gl.vm.UserError(f"Query '{query_id}' not found")
        q = self.queries[query_id]
        return {
            "creator": q.creator,
            "created_at": q.created_at,
            "json_path": q.json_path,
            "tolerance_bps": q.tolerance_bps,
            "threshold_count": q.threshold_count,
            "source_count": q.source_count,
            "resolved": q.resolved,
            "agreed_value": q.agreed_value,
            "agreeing_count": q.agreeing_count,
        }

    @gl.public.view
    def get_sources(self, query_id: str) -> list:
        return list(self.query_sources.get(query_id, []))
