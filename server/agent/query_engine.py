"""
Enterprise AI Hub - In-Memory Safe Query Engine
================================================
Translates a high-level JSON plan produced by the LLM planner into safe, exact
Python aggregations over the user's live authorized dataset.

Features:
  - Strict sandbox: never uses eval(), exec(), or arbitrary code.
  - Operations supported:
      * filter: ==, !=, >, >=, <, <=, in, not in, contains, startswith, is_null, not_null, between
      * group_by: 1 to 3 columns
      * aggregates: count, count_distinct, sum, avg, min, max
      * sort: asc / desc, by any column or aggregate
      * limit / top-N / bottom-N
      * pivot: row_dim, col_dim, val_dim, agg (e.g. city x department x avg(salary))
      * compute KPIs: total_rows, overall sums, averages, max/min
  - Automatic type coercion (handles "$120,000", "75,000.50", "2026-01-05T00:00:00Z").
  - Produces both:
      1. Clean Markdown table with exact numbers
      2. Apache ECharts JSON config (exact numbers, properly formatted)
      3. KPI dictionary for the UI cards
"""

import re
import difflib
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union, Set

try:
    import duckdb
    import pandas as pd
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False


# ---------------------------------------------------------------------------
# Value Parsing & Cleaning
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"^[+-]?[0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?$|^[+-]?[0-9]+(?:\.[0-9]+)?$")
_CURR_RE = re.compile(r"^[^\w\s]*\s*([+-]?[0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?|[+-]?[0-9]+(?:\.[0-9]+)?)\s*[^\w\s]*$")


def parse_number(val: Any) -> Optional[float]:
    """Extract a float from numeric strings, currency symbols, commas. Returns None if not numeric."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        return None
    # Quick strip common currency prefixes/suffixes: PKR, Rs, $, EUR, commas
    cleaned = re.sub(r"^(?:pkr|rs\.?|usd|\$|€|£)\s*", "", s, flags=re.I)
    cleaned = re.sub(r"\s*(?:pkr|rs\.?|usd|\$|€|£)$", "", cleaned, flags=re.I).strip()
    cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_date(val: Any) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    s = str(val).strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%d-%b-%Y",
        "%d-%m-%Y",
        "%m/%d/%Y",
        "%d/%m/%Y",
    ):
        try:
            return datetime.strptime(s.split(".")[0].rstrip("Z"), fmt.rstrip("Z"))
        except ValueError:
            pass
    return None


def format_number(val: Any) -> str:
    """Friendly formatting for display: integers comma-separated, floats rounded to 2 decimals."""
    if val is None:
        return ""
    if isinstance(val, float):
        if val.is_integer():
            return f"{int(val):,}"
        return f"{val:,.2f}"
    if isinstance(val, int):
        return f"{val:,}"
    return str(val)


# ---------------------------------------------------------------------------
# Filter Sandbox
# ---------------------------------------------------------------------------

def normalize_text(val: Any) -> str:
    """Normalize string by removing excess whitespace and replacing underscores/hyphens with spaces."""
    if val is None:
        return ""
    s = str(val).strip().lower()
    return re.sub(r"[\s_\-]+", " ", s)


def _compare(cell: Any, op: str, target: Any) -> bool:
    op = (op or "==").lower().strip()
    # Null checks
    if op in ("is_null", "is null", "null"):
        return cell is None or str(cell).strip() == ""
    if op in ("not_null", "not null", "not_null", "present"):
        return cell is not None and str(cell).strip() != ""

    if cell is None:
        return False

    # Try numeric compare first
    c_num = parse_number(cell)
    t_num = parse_number(target) if not isinstance(target, list) else None

    if c_num is not None and t_num is not None and op in ("==", "!=", ">", ">=", "<", "<="):
        if op == "==": return abs(c_num - t_num) < 1e-9
        if op == "!=": return abs(c_num - t_num) >= 1e-9
        if op == ">":  return c_num > t_num
        if op == ">=": return c_num >= t_num
        if op == "<":  return c_num < t_num
        if op == "<=": return c_num <= t_num

    # Date compare
    c_dt = parse_date(cell)
    t_dt = parse_date(target) if not isinstance(target, list) else None
    if c_dt is not None and t_dt is not None and op in ("==", "!=", ">", ">=", "<", "<="):
        if op == "==": return c_dt == t_dt
        if op == "!=": return c_dt != t_dt
        if op == ">":  return c_dt > t_dt
        if op == ">=": return c_dt >= t_dt
        if op == "<":  return c_dt < t_dt
        if op == "<=": return c_dt <= t_dt

    # Between
    if op == "between" and isinstance(target, (list, tuple)) and len(target) == 2:
        lo, hi = parse_number(target[0]), parse_number(target[1])
        if c_num is not None and lo is not None and hi is not None:
            return lo <= c_num <= hi
        return str(target[0]).lower() <= str(cell).lower() <= str(target[1]).lower()

    # In / Not in
    if op in ("in", "not in"):
        targets = target if isinstance(target, list) else [target]
        targets_lower = {str(x).strip().lower() for x in targets}
        c_str = str(cell).strip().lower()
        # also match numeric equivalence
        matched = c_str in targets_lower
        if not matched and c_num is not None:
            t_nums = {parse_number(x) for x in targets}
            matched = any(tn is not None and abs(c_num - tn) < 1e-9 for tn in t_nums)
        return matched if op == "in" else not matched

    # String operations (case-insensitive + delimiter normalization + fuzzy fallback)
    s_cell = str(cell).strip().lower()
    s_target = str(target).strip().lower()
    n_cell = normalize_text(cell)
    n_target = normalize_text(target)

    if op in ("==", "eq", "equals"):
        if s_cell == s_target or n_cell == n_target:
            return True
        # Fuzzy match for minor typos or formatting differences (e.g. "instock" vs "in stock")
        if len(n_target) >= 4 and len(n_cell) >= 4:
            ratio = difflib.SequenceMatcher(None, n_cell, n_target).ratio()
            if ratio >= 0.84:
                return True
        # Compound abbreviation or word-part match (e.g. target "prog" in "IT_PROG")
        if len(n_target) >= 3 and (n_target in n_cell or n_cell in n_target):
            return True
        return False

    if op in ("!=", "neq", "not_equals"):
        return s_cell != s_target and n_cell != n_target

    if op in ("contains", "like"):
        return s_target in s_cell or n_target in n_cell

    if op in ("startswith", "starts_with"):
        return s_cell.startswith(s_target) or n_cell.startswith(n_target)

    if op in ("endswith", "ends_with"):
        return s_cell.endswith(s_target) or n_cell.endswith(n_target)

    return False



# ---------------------------------------------------------------------------
# Self-Healing Categorical Sanitizer & Fuzzy Domain Matcher
# ---------------------------------------------------------------------------

class CategoricalSanitizer:
    """
    Zero-Hallucination Categorical Value Sanitizer for In-Memory Analytical Engines.
    Maps corrupted, accented, differently cased, or hyphenated user filters to true database column profiles.
    (e.g., handles 'IN STOCK' / 'in-stock' -> 'IN_STOCK', 'instok' -> 'IN_STOCK').
    """

    @staticmethod
    def canonical_slug(val: Any) -> str:
        """Transforms arbitrary values into normalized alphanumeric comparison tokens."""
        if val is None:
            return ""
        s = str(val).strip().lower()
        return re.sub(r"[^a-z0-9]", "", s)

    @classmethod
    def sanitize_filter_value(
        cls,
        raw_val: Any,
        candidate_domain: Union[List[str], Set[str]],
        similarity_threshold: float = 0.78
    ) -> Tuple[Any, bool, float]:
        """
        Sanitizes a candidate value against an authoritative categorical domain.
        Returns: (repaired_value, was_modified, confidence_score)
        """
        if raw_val is None or not candidate_domain:
            return raw_val, False, 0.0

        raw_str = str(raw_val).strip()
        canon_raw = cls.canonical_slug(raw_str)

        if not canon_raw:
            return raw_val, False, 0.0

        candidates = [str(c) for c in candidate_domain if c is not None]

        # 1. Exact Literal Identity
        if raw_str in candidates:
            return raw_str, False, 1.0

        # 2. Case-Insensitive Matching
        for cand in candidates:
            if raw_str.lower() == cand.lower():
                return cand, True, 1.0

        # 3. Canonical Slug Exact Matching (e.g. 'in-stock' -> 'instock' <- 'IN_STOCK')
        for cand in candidates:
            if canon_raw == cls.canonical_slug(cand):
                return cand, True, 0.98

        # 4. Token-Permutation Matching (e.g. 'Stock In' == 'In Stock')
        raw_tokens = set(re.split(r"[\s_\-]+", raw_str.lower()))
        for cand in candidates:
            cand_tokens = set(re.split(r"[\s_\-]+", cand.lower()))
            if raw_tokens == cand_tokens and raw_tokens:
                return cand, True, 0.95

        # 5. Sequence Matcher (Typo & Levenshtein Proximity)
        best_cand: Optional[str] = None
        best_score: float = 0.0

        for cand in candidates:
            canon_cand = cls.canonical_slug(cand)
            score = difflib.SequenceMatcher(None, canon_raw, canon_cand).ratio()
            if score > best_score:
                best_score = score
                best_cand = cand

        if best_cand and best_score >= similarity_threshold:
            return best_cand, True, round(best_score, 4)

        return raw_val, False, 0.0


def self_heal_plan_filters(
    dataset_rows: List[dict],
    filters: List[dict],
    similarity_threshold: float = 0.78
) -> Tuple[List[dict], List[str]]:
    """
    Scans the in-memory dataset to extract distinct domain profiles for filtered columns,
    repairing faulty planner parameters before executing queries to eliminate 0-row errors.
    """
    if not dataset_rows or not filters:
        return filters, []

    audit_logs: List[str] = []
    healed_filters: List[dict] = []

    active_cols = {str(f.get("col") or f.get("field", "")).strip().lower() for f in filters if f.get("col") or f.get("field")}
    column_profiles: Dict[str, Set[str]] = {c: set() for c in active_cols}

    # Extract distinct categorical profiles from dataset
    for row in dataset_rows:
        for k, val in row.items():
            k_low = k.strip().lower()
            if k_low in column_profiles and val is not None and not isinstance(val, (int, float, dict, list)):
                column_profiles[k_low].add(str(val))

    for f in filters:
        col = str(f.get("col") or f.get("field", "")).strip()
        col_low = col.lower()
        op = (f.get("op") or "==").strip().lower()
        val = f.get("val") if "val" in f else f.get("value")

        if col_low in column_profiles and op in ("==", "eq", "!=", "neq", "in", "contains"):
            candidates = column_profiles[col_low]
            if isinstance(val, list):
                new_vals = []
                for v in val:
                    fixed_v, modified, conf = CategoricalSanitizer.sanitize_filter_value(
                        v, candidates, similarity_threshold
                    )
                    if modified:
                        audit_logs.append(f"Healed [{col}] item '{v}' -> '{fixed_v}' (Conf: {conf})")
                    new_vals.append(fixed_v)
                f_copy = dict(f)
                f_copy["val"] = new_vals
                healed_filters.append(f_copy)
            else:
                fixed_val, modified, conf = CategoricalSanitizer.sanitize_filter_value(
                    val, candidates, similarity_threshold
                )
                if modified:
                    audit_logs.append(f"Healed [{col}] filter value '{val}' -> '{fixed_val}' (Conf: {conf})")
                f_copy = dict(f)
                f_copy["val"] = fixed_val
                healed_filters.append(f_copy)
        else:
            healed_filters.append(f)

    return healed_filters, audit_logs

def apply_filters(rows: List[dict], filters: List[dict]) -> List[dict]:
    """filters: [{"col": "status", "op": "==", "val": "Active"}, ...]"""
    if not filters:
        return rows
    # Self-heal incoming filters against dataset domain values (e.g. 'in-stock' -> 'IN_STOCK')
    filters, _ = self_heal_plan_filters(rows, filters)
    out = []
    for r in rows:
        match = True
        for f in filters:
            col = f.get("col") or f.get("field")
            op = f.get("op", "==")
            val = f.get("val") if "val" in f else f.get("value")
            # Case-insensitive column lookup
            val_in_row = None
            for k in r:
                if k.lower() == str(col).lower():
                    val_in_row = r[k]
                    break
            if not _compare(val_in_row, op, val):
                match = False
                break
        if match:
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# Aggregation Engine
# ---------------------------------------------------------------------------

def _run_agg(values: List[Any], agg: str) -> Optional[float]:
    agg = (agg or "count").lower().strip()
    if agg == "count":
        return len(values)
    if agg == "count_distinct":
        return len({str(v) for v in values if v is not None and str(v).strip() != ""})

    nums = [parse_number(v) for v in values]
    nums = [n for n in nums if n is not None]
    if not nums:
        return 0 if agg in ("sum", "count") else None

    if agg == "sum":   return round(sum(nums), 4)
    if agg in ("avg", "average", "mean"): return round(sum(nums) / len(nums), 4)
    if agg == "min":   return min(nums)
    if agg == "max":   return max(nums)
    return None


def aggregate(
    rows: List[dict],
    group_by: List[str],
    aggs: List[dict],
) -> List[dict]:
    """
    group_by: ["department", "city"]
    aggs: [
      {"col": "*", "agg": "count", "as": "Employee Count"},
      {"col": "salary", "agg": "avg", "as": "Average Salary"},
      {"col": "salary", "agg": "sum", "as": "Total Salary"},
    ]
    """
    if not rows:
        return []

    # Map actual column names case-insensitively
    first = rows[0]
    actual_cols = {c.lower(): c for c in first.keys()}

    resolved_gb = [actual_cols.get(g.lower(), g) for g in group_by]

    # Group rows
    groups: Dict[Tuple, List[dict]] = {}
    for r in rows:
        key = tuple(r.get(c) for c in resolved_gb)
        groups.setdefault(key, []).append(r)

    # Compute aggregates for each group
    out = []
    for key, g_rows in groups.items():
        row_dict = {}
        for c, k_val in zip(resolved_gb, key):
            row_dict[c] = k_val

        for a in aggs:
            col_name = a.get("col", "*")
            agg_type = a.get("agg", "count")
            alias = a.get("as") or f"{agg_type.upper()}({col_name})"
            if col_name == "*":
                vals = [1] * len(g_rows)
            else:
                real_col = actual_cols.get(str(col_name).lower(), col_name)
                vals = [r.get(real_col) for r in g_rows]
            row_dict[alias] = _run_agg(vals, agg_type)

        out.append(row_dict)

    return out


# ---------------------------------------------------------------------------
# Pivot (for 2-dimensional charts & matrix tables)
# ---------------------------------------------------------------------------

def pivot(
    rows: List[dict],
    row_dim: str,
    col_dim: str,
    val_dim: str,
    agg: str = "sum",
) -> Tuple[List[str], List[dict]]:
    """
    Creates a 2D cross-tab:
    e.g. row_dim='city', col_dim='department', val_dim='salary', agg='avg'
    Returns: (columns_list, result_rows)
    """
    if not rows:
        return [], []

    first = rows[0]
    actual_cols = {c.lower(): c for c in first.keys()}
    r_col = actual_cols.get(row_dim.lower(), row_dim)
    c_col = actual_cols.get(col_dim.lower(), col_dim)
    v_col = actual_cols.get(val_dim.lower(), val_dim)

    # Collect distinct column values
    col_values = sorted({str(r.get(c_col) or "Unknown") for r in rows})

    # Group by (r_val, c_val)
    cells: Dict[Tuple[Any, str], List[Any]] = {}
    row_keys = []
    seen_row_keys = set()
    for r in rows:
        rk = r.get(r_col)
        if rk not in seen_row_keys:
            seen_row_keys.add(rk)
            row_keys.append(rk)
        ck = str(r.get(c_col) or "Unknown")
        cells.setdefault((rk, ck), []).append(r.get(v_col))

    result_rows = []
    for rk in row_keys:
        row = {r_col: rk}
        for ck in col_values:
            vals = cells.get((rk, ck), [])
            row[ck] = _run_agg(vals, agg) if vals else 0
        result_rows.append(row)

    headers = [r_col] + col_values
    return headers, result_rows


# ---------------------------------------------------------------------------
# Sorting & Limit
# ---------------------------------------------------------------------------

def sort_rows(rows: List[dict], order_by: List[dict]) -> List[dict]:
    """order_by: [{"col": "Total Salary", "desc": True}]"""
    if not order_by or not rows:
        return rows

    def sort_key(row):
        keys = []
        for o in order_by:
            col = o.get("col")
            desc = o.get("desc", False)
            v = None
            for k in row:
                if k.lower() == str(col).lower():
                    v = row[k]
                    break
            num = parse_number(v)
            if num is not None:
                # Use number
                val = (-num if desc else num)
            elif v is None:
                val = ""
            else:
                val = str(v).lower()
                if desc:
                    # Negate string sort by wrapping in custom reverse wrapper
                    val = _ReverseString(val)
            keys.append(val)
        return tuple(keys)

    class _ReverseString:
        def __init__(self, s): self.s = s
        def __lt__(self, o): return self.s > o.s
        def __gt__(self, o): return self.s < o.s
        def __eq__(self, o): return self.s == o.s

    return sorted(rows, key=sort_key)


# ---------------------------------------------------------------------------
# KPI Summary Calculator
# ---------------------------------------------------------------------------

def compute_kpis(rows: List[dict], num_cols: Optional[List[str]] = None) -> List[dict]:
    """
    Computes 2 to 4 key cards from the dataset:
    [{"label": "Total Employees", "value": "150", "sub": "across 4 cities"}, ...]
    """
    if not rows:
        return []

    first = rows[0]
    cards = [{"label": "Total Records", "value": f"{len(rows):,}", "sub": "rows analyzed"}]

    # Detect numeric columns if not supplied
    if not num_cols:
        candidate_cols = []
        for k in first:
            lk = str(k).lower().strip()
            # Exclude IDs, codes, links, and contact numbers from numeric KPIs
            if lk == "id" or lk.endswith("_id") or lk.startswith("id_") or lk in (
                "emp_id", "user_id", "dept_id", "deptid", "product_id", "access_id",
                "links", "phone", "contact", "code", "zip", "postal", "extension"
            ):
                continue
            vals = [parse_number(r.get(k)) for r in rows[:20]]
            if len([v for v in vals if v is not None]) >= 10:
                candidate_cols.append(k)
        num_cols = candidate_cols

    for col in num_cols[:2]:
        nums = [parse_number(r.get(col)) for r in rows]
        nums = [n for n in nums if n is not None]
        if not nums:
            continue
        total = sum(nums)
        avg = total / len(nums)
        col_title = col.replace("_", " ").title()
        if total > 1000 or "salary" in col.lower() or "price" in col.lower() or "amount" in col.lower():
            cards.append({
                "label": f"Total {col_title}",
                "value": format_number(round(total, 2)),
                "sub": f"Avg: {format_number(round(avg, 2))}"
            })
        else:
            cards.append({
                "label": f"Avg {col_title}",
                "value": format_number(round(avg, 2)),
                "sub": f"Min: {format_number(min(nums))} · Max: {format_number(max(nums))}"
            })

    return cards[:4]


# ---------------------------------------------------------------------------
# Smart Column Resolution for Charts & Visualizations
# ---------------------------------------------------------------------------

def is_id_or_code_col(col_name: str) -> bool:
    """Returns True if the column is an ID, code, phone, zip, or surrogate key that should not be plotted as a numeric metric."""
    if not col_name:
        return False
    c = str(col_name).lower().strip()
    if c in ("id", "code", "zip", "postal_code", "year", "year_built", "phone", "phone_number", "ssn", "place_id", "emp_id", "dept_id", "product_id", "user_id", "customer_id", "vendor_id"):
        return True
    if c.endswith("_id") or c.startswith("id_") or c.endswith("_pk") or c.endswith("_fk"):
        return True
    if c.endswith("id") and len(c) > 2 and c not in ("paid", "mid", "solid", "fluid", "hybrid", "valid", "grid"):
        return True
    return False

def find_best_category_col(row: dict, requested_cat: Optional[str] = None) -> str:
    """
    Finds the best descriptive human-readable name column across ANY table.
    Prioritizes name/title/category columns ('full_name', 'product_name', 'business_name', 'name', 'department', 'category')
    over raw numeric ID codes ('emp_id', 'product_id', 'id', 'dept_id').
    """
    if not row:
        return requested_cat or "Category"
    keys = list(row.keys())

    # If requested_cat was given and is NOT an ID column, keep it if it exists in the row
    if requested_cat:
        for k in keys:
            if k.lower() == str(requested_cat).lower() and not is_id_or_code_col(k):
                return k

    # 1. Exact priority candidates for human-readable name columns across tables
    priority_names = [
        "full_name", "employee_name", "product_name", "business_name",
        "name", "first_name", "product", "item_name", "lead_name",
        "title", "job_title", "dept_name", "department_name", "category_name",
        "category", "department", "niche", "city", "status", "location"
    ]
    for p in priority_names:
        for k in keys:
            if k.lower() == p:
                return k

    # 2. Any column containing 'name' or 'title' that is not an ID, URL, or link
    for k in keys:
        kl = k.lower()
        if ("name" in kl or "title" in kl) and not is_id_or_code_col(k) and "url" not in kl and "link" not in kl:
            return k

    # 3. Any descriptive text categorical column (category, department, city, niche, role, status)
    for k in keys:
        kl = k.lower()
        if any(w in kl for w in ["category", "department", "dept", "city", "niche", "role", "status", "type"]):
            if not is_id_or_code_col(k):
                return k

    # 4. Any string column with moderate length (<60 chars) that is not an ID, email, url, date
    for k in keys:
        if not is_id_or_code_col(k) and not any(skip in k.lower() for skip in ["email", "url", "link", "phone", "image", "file", "avatar", "date", "created", "updated"]):
            val = row.get(k)
            if isinstance(val, str) and not val.replace('.', '', 1).isdigit() and len(val) < 60:
                return k

    # Fallback to requested_cat or first non-empty key
    return requested_cat or keys[0]

def find_best_value_cols(row: dict, cat_col: str, requested_vals: Optional[List[str]] = None) -> List[str]:
    """
    Finds real numeric metric columns (salary, price, stock, rating, amount)
    and strictly excludes ID columns (dept_id, emp_id, etc.).
    """
    if not row:
        return requested_vals or []
    keys = list(row.keys())

    # If requested_vals provided, filter out any ID columns
    if requested_vals:
        clean = [v for v in requested_vals if any(k.lower() == str(v).lower() for k in keys) and str(v).lower() != str(cat_col).lower() and not is_id_or_code_col(v)]
        valid_numeric = []
        for v in clean:
            actual_k = next(k for k in keys if k.lower() == str(v).lower())
            if parse_number(row.get(actual_k)) is not None:
                valid_numeric.append(actual_k)
        if valid_numeric:
            return valid_numeric

    # Auto-detect numeric columns that are NOT IDs
    metric_cols = []
    for k in keys:
        if k.lower() == str(cat_col).lower():
            continue
        if is_id_or_code_col(k):
            continue
        val = row.get(k)
        if parse_number(val) is not None:
            metric_cols.append(k)

    if metric_cols:
        prio_metrics = ["salary", "price", "stock_qty", "stock_quantity", "stock", "total", "amount", "rating", "reviews_count", "revenue", "cost", "budget", "score"]
        sorted_metrics = sorted(metric_cols, key=lambda m: next((i for i, p in enumerate(prio_metrics) if p in m.lower()), 99))
        return sorted_metrics[:2]

    fallback = [k for k in keys if k.lower() != str(cat_col).lower() and parse_number(row.get(k)) is not None]
    return fallback[:1] if fallback else []


# ---------------------------------------------------------------------------
# ECharts Generation
# ---------------------------------------------------------------------------

def build_echarts(
    chart_type: str,
    title: str,
    data_rows: List[dict],
    cat_col: str,
    val_cols: List[str],
    subtext: str = "",
) -> Optional[dict]:
    """
    chart_type: 'bar', 'donut', 'line', 'rose', 'stacked_bar'
    """
    if not data_rows or not cat_col or not val_cols:
        return None

    first_row = data_rows[0]

    # 1. Cleanse val_cols: strictly filter out ID / code columns (e.g. dept_id, emp_id)
    val_cols = find_best_value_cols(first_row, cat_col, val_cols)
    if not val_cols:
        return None

    # 2. Human-readable name column resolution across any table
    keys = list(first_row.keys())
    name_col = find_best_category_col(first_row, None)
    id_col = next((k for k in keys if is_id_or_code_col(k)), None)
    is_id_axis = is_id_or_code_col(cat_col)

    # If cat_col is an ID column and a descriptive name column exists, prefer the name column
    if is_id_axis and name_col and not is_id_or_code_col(name_col):
        cat_col_actual = name_col
        if " by " in title:
            base_t = title.split(" by ")[0]
            title = f"{base_t} by {name_col.replace('_', ' ').title()}"
    else:
        cat_col_actual = None
        for k in first_row:
            if k.lower() == str(cat_col).lower():
                cat_col_actual = k
                break
        if not cat_col_actual:
            cat_col_actual = cat_col

    # Prepare extra associated name mapping for tooltips
    extra_names = [str(r.get(name_col) or "") if name_col else "" for r in data_rows[:25]]
    extra_ids = [str(r.get(id_col) or "") if id_col else "" for r in data_rows[:25]]
    has_extra_names = bool(name_col and any(extra_names))

    # If data_rows has duplicate categories (e.g. raw table rows), aggregate them in-memory
    cats_seen = [r.get(cat_col_actual) for r in data_rows]
    if len(cats_seen) != len(set(cats_seen)) and len(data_rows) > 1:
        agg_map = {}
        for r in data_rows:
            c_val = str(r.get(cat_col_actual) or "Unknown")
            if c_val not in agg_map:
                agg_map[c_val] = {vc: 0.0 for vc in val_cols}
                agg_map[c_val]["__count__"] = 0
                if has_extra_names:
                    agg_map[c_val]["__extra_name__"] = str(r.get(name_col) or "")
            for vc in val_cols:
                v_num = parse_number(r.get(vc))
                if v_num is not None:
                    agg_map[c_val][vc] += v_num
            agg_map[c_val]["__count__"] += 1
        new_rows = []
        for c_val, metrics in agg_map.items():
            row_dict = {cat_col_actual: c_val}
            for vc in val_cols:
                row_dict[vc] = round(metrics[vc], 2)
            row_dict["Count"] = metrics["__count__"]
            if has_extra_names:
                row_dict[name_col] = metrics.get("__extra_name__", "")
            new_rows.append(row_dict)
        data_rows = sorted(new_rows, key=lambda x: parse_number(x.get(val_cols[0])) or 0, reverse=True)
        if has_extra_names:
            extra_names = [str(r.get(name_col) or "") for r in data_rows[:25]]

    chart_type = (chart_type or "bar").lower().strip()

    # Donut / Rose (single value column)
    if chart_type in ("donut", "pie", "rose"):
        v_col = val_cols[0]
        points = []
        for r in data_rows[:15]:
            v = parse_number(r.get(v_col))
            if v is not None:
                id_val = str(r.get(cat_col_actual) or "Unknown")
                n_val = str(r.get(name_col) or "") if has_extra_names else ""
                display_name = f"{id_val} — {n_val}" if (is_id_axis and n_val) else (n_val or id_val)
                points.append({
                    "name": display_name,
                    "value": round(v, 2),
                    "item_name": n_val or id_val,
                    "raw_id": id_val
                })
        series = {"type": "pie", "data": points}
        if chart_type == "rose":
            series["roseType"] = "area"
        return {
            "title": {"text": title, "subtext": subtext or f"by {cat_col_actual.replace('_', ' ')}"},
            "series": [series],
            "extra_names": [p.get("item_name", "") for p in points]
        }

    # Bar / Line / Grouped / Stacked
    categories = [str(r.get(cat_col_actual) or "") for r in data_rows[:25]]
    series_list = []
    for vc in val_cols:
        v_actual = None
        for k in first_row:
            if k.lower() == vc.lower():
                v_actual = k
                break
        v_actual = v_actual or vc

        values = []
        for r in data_rows[:25]:
            val = parse_number(r.get(v_actual))
            num = round(val, 2) if val is not None else 0
            n_val = str(r.get(name_col) or "") if name_col else ""
            i_val = str(r.get(id_col) or "") if id_col else ""
            cat_val = str(r.get(cat_col_actual) or "")
            values.append({
                "value": num,
                "name": cat_val,
                "item_name": n_val,
                "raw_name": n_val,
                "raw_id": i_val
            })

        s_obj = {
            "name": v_actual.replace("_", " ").title(),
            "type": "line" if chart_type == "line" else "bar",
            "data": values
        }
        if chart_type == "stacked_bar":
            s_obj["stack"] = "total"
        series_list.append(s_obj)

    # If exactly 2 series have very different scales (e.g. employee count vs total salary),
    # use dual y-axes so neither series is crushed flat in the chart!
    if len(series_list) == 2 and chart_type != "stacked_bar":
        max0 = max([abs(v["value"] if isinstance(v, dict) else v) for v in series_list[0]["data"]] or [1])
        max1 = max([abs(v["value"] if isinstance(v, dict) else v) for v in series_list[1]["data"]] or [1])
        ratio = max(max0, max1) / max(min(max0, max1), 0.001)
        if ratio > 2.5:
            series_list[0]["yAxisIndex"] = 0
            series_list[1]["yAxisIndex"] = 1
            return {
                "title": {"text": title, "subtext": subtext},
                "tooltip": {"trigger": "axis"},
                "legend": {"data": [s["name"] for s in series_list]},
                "xAxis": {"type": "category", "data": categories, "name": cat_col_actual.replace("_", " ").title()},
                "yAxis": [
                    {"type": "value", "name": series_list[0]["name"]},
                    {"type": "value", "name": series_list[1]["name"], "splitLine": {"show": False}}
                ],
                "series": series_list,
                "extra_names": extra_names
            }

    return {
        "title": {"text": title, "subtext": subtext},
        "xAxis": {"type": "category", "data": categories, "name": cat_col_actual.replace("_", " ").title()},
        "yAxis": {"type": "value"},
        "series": series_list,
        "extra_names": extra_names,
        "extra_ids": extra_ids
    }


# ---------------------------------------------------------------------------
# Markdown Table Formatter
# ---------------------------------------------------------------------------

def _clean_cell(val: Any) -> str:
    if val is None:
        return ""
    formatted = format_number(val)
    # Sanitize markdown table breaking characters: newlines, carriage returns, and pipes
    return str(formatted).replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("|", "&#124;").strip()

def rows_to_md_table(rows: List[dict], max_rows: int = 50) -> str:
    if not rows:
        return "_No matching records found._"
    cols = [c for c in rows[0].keys() if c != "links"]
    head = "| " + " | ".join(c.replace("_", " ").title() for c in cols) + " |"
    sep = "|" + "|".join("---" for _ in cols) + "|"
    lines = [head, sep]
    for r in rows[:max_rows]:
        line = "| " + " | ".join(_clean_cell(r.get(c)) for c in cols) + " |"
        lines.append(line)
    if len(rows) > max_rows:
        lines.append(f"\n_Showing first {max_rows} of {len(rows):,} records in chat preview. Use Table Explorer or Export to Excel for the complete dataset._")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main Plan Execution Dispatcher
# ---------------------------------------------------------------------------

def execute_duckdb_plan(dataset: List[dict], plan: dict) -> Optional[List[dict]]:
    """
    High-speed vectorized execution path via DuckDB in-memory engine.
    Executes filtering, aggregation, sorting, and limits over 100,000+ rows in milliseconds.
    """
    if not HAS_DUCKDB or not dataset:
        return None
    try:
        con = duckdb.connect(database=":memory:")
        df = pd.DataFrame(dataset)
        con.register("dataset", df)

        actual_cols = {c.lower(): c for c in df.columns}

        # Build WHERE clause
        where_clauses = []
        for f in plan.get("filters") or []:
            col = f.get("col") or f.get("field")
            op = (f.get("op") or "==").lower().strip()
            val = f.get("val") if "val" in f else f.get("value")
            real_col = actual_cols.get(str(col).lower())
            if not real_col:
                continue

            safe_col = f'"{real_col}"'
            if op in ("is_null", "is null", "null"):
                where_clauses.append(f"{safe_col} IS NULL OR TRIM(CAST({safe_col} AS VARCHAR)) = ''")
            elif op in ("not_null", "not null", "present"):
                where_clauses.append(f"{safe_col} IS NOT NULL AND TRIM(CAST({safe_col} AS VARCHAR)) != ''")
            elif op in ("==", "eq", "equals"):
                clean_val = str(val).replace("'", "''").strip().lower()
                clean_norm = re.sub(r"[\s_\-]+", " ", clean_val)
                where_clauses.append(
                    f"(LOWER(CAST({safe_col} AS VARCHAR)) = '{clean_val}' OR "
                    f"REGEXP_REPLACE(LOWER(CAST({safe_col} AS VARCHAR)), '[\\s_\\-]+', ' ', 'g') = '{clean_norm}')"
                )
            elif op in ("!=", "neq"):
                clean_val = str(val).replace("'", "''").strip().lower()
                where_clauses.append(f"LOWER(CAST({safe_col} AS VARCHAR)) != '{clean_val}'")
            elif op in (">", ">=", "<", "<="):
                try:
                    num_val = float(str(val).replace(",", "").replace("$", ""))
                    col_num = f"""TRY_CAST(REGEXP_REPLACE(CAST("{real_c}" AS VARCHAR), '[^0-9.-]', '', 'g') AS DOUBLE)"""
                except Exception:
                    clean_v = str(val).replace("'", "''")
                    where_clauses.append(f"{safe_col} {op} '{clean_v}'")
            elif op in ("contains", "like"):
                clean_val = str(val).replace("'", "''").strip().lower()
                where_clauses.append(f"LOWER(CAST({safe_col} AS VARCHAR)) LIKE '%{clean_val}%'")
            elif op in ("startswith", "starts_with"):
                clean_val = str(val).replace("'", "''").strip().lower()
                where_clauses.append(f"LOWER(CAST({safe_col} AS VARCHAR)) LIKE '{clean_val}%'")

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        mode = plan.get("mode", "aggregate" if plan.get("group_by") else "raw")

        # Build SELECT & GROUP BY
        if mode in ("aggregate", "join") and plan.get("group_by"):
            gb_cols = []
            for g in plan["group_by"]:
                real_g = actual_cols.get(str(g).lower(), g)
                gb_cols.append(f'"{real_g}"')

            select_parts = list(gb_cols)
            for a in plan.get("aggs") or [{"col": "*", "agg": "count", "as": "Count"}]:
                col_name = a.get("col", "*")
                agg_op = str(a.get("agg") or "count").upper()
                alias = a.get("as") or f"{agg_op}_{col_name}"
                safe_alias = f'"{alias}"'

                if col_name == "*" or agg_op == "COUNT":
                    select_parts.append(f"COUNT(*) AS {safe_alias}")
                elif agg_op == "COUNT_DISTINCT":
                    real_c = actual_cols.get(str(col_name).lower(), col_name)
                    select_parts.append(f'COUNT(DISTINCT "{real_c}") AS {safe_alias}')
                else:
                    real_c = actual_cols.get(str(col_name).lower(), col_name)
                    col_num = f"""TRY_CAST(REGEXP_REPLACE(CAST("{real_c}" AS VARCHAR), '[^0-9.-]', '', 'g') AS DOUBLE)"""
                    if agg_op in ("SUM", "AVG", "MIN", "MAX"):
                        select_parts.append(f'{agg_op}({col_num}) AS {safe_alias}')
                    else:
                        select_parts.append(f'COUNT("{real_c}") AS {safe_alias}')

            query = f"SELECT {', '.join(select_parts)} FROM dataset {where_sql} GROUP BY {', '.join(gb_cols)}"
        else:
            cols = plan.get("select_cols")
            if cols:
                sel = [f'"{actual_cols.get(c.lower(), c)}"' for c in cols if c.lower() in actual_cols]
                sel_sql = ", ".join(sel) if sel else "*"
            else:
                sel_sql = "*"
            query = f"SELECT {sel_sql} FROM dataset {where_sql}"

        # ORDER BY
        if plan.get("order_by"):
            order_parts = []
            for o in plan["order_by"]:
                c = o.get("col")
                desc = "DESC" if o.get("desc", False) else "ASC"
                order_parts.append(f'"{c}" {desc}')
            if order_parts:
                query += f" ORDER BY {', '.join(order_parts)}"

        # LIMIT
        if plan.get("limit"):
            query += f" LIMIT {int(plan['limit'])}"

        res_df = con.execute(query).df()
        res_df = res_df.where(pd.notnull(res_df), None)
        return res_df.to_dict(orient="records")
    except Exception as e:
        return None


def execute_plan(dataset: List[dict], plan: dict) -> dict:
    """
    Executes a structured JSON plan produced by the planner LLM.
    Returns:
      {
        "table_rows": List[dict],
        "table_md": str,
        "kpis": List[dict],
        "chart": Optional[dict],
        "row_count": int,
        "mode": "raw" | "aggregate" | "pivot"
      }
    """
    # Self-heal plan filters against dataset categorical domain before DuckDB or Python execution
    if plan.get("filters") and dataset:
        healed_filters, _ = self_heal_plan_filters(dataset, plan["filters"])
        plan["filters"] = healed_filters

    rows = dataset
    mode = plan.get("mode", "aggregate" if plan.get("group_by") else "raw")
    chart_obj = None
    table_rows = None

    # Step 1: High-Speed Vectorized In-Memory DuckDB Fast-Path (sub-50ms for large datasets)
    if HAS_DUCKDB and len(dataset) >= 50 and mode != "pivot":
        table_rows = execute_duckdb_plan(dataset, plan)

    # Step 2: Fallback to Pure Python Sandboxed In-Memory Engine if DuckDB path skipped or failed
    if table_rows is None:
        rows = dataset
        filters = plan.get("filters") or []
        if filters:
            rows = apply_filters(rows, filters)

        if mode == "pivot" and plan.get("pivot"):
            p = plan["pivot"]
            headers, table_rows = pivot(
                rows,
                p.get("row_dim"),
                p.get("col_dim"),
                p.get("val_dim"),
                p.get("agg", "sum")
            )
        elif plan.get("group_by") or (mode == "aggregate" and plan.get("aggs")):
            gb = []
            for g in (plan.get("group_by") or []):
                if str(g).lower() in ("dept_id", "deptid") and rows and any(k.lower() in ("department", "dept_name") for k in rows[0]):
                    dept_col = next(k for k in rows[0] if k.lower() in ("department", "dept_name"))
                    gb.append(dept_col)
                else:
                    gb.append(g)
            table_rows = aggregate(
                rows,
                gb,
                plan.get("aggs") or [{"col": "*", "agg": "count", "as": "Count"}]
            )
        else:
            cols = plan.get("select_cols")
            if cols and rows:
                actual = {c.lower(): c for c in rows[0].keys()}
                selected = [actual.get(c.lower(), c) for c in cols if c.lower() in actual]
                table_rows = [{c: r.get(c) for c in selected} for r in rows]
            else:
                table_rows = rows

        if plan.get("order_by"):
            table_rows = sort_rows(table_rows, plan["order_by"])

        if plan.get("limit"):
            table_rows = table_rows[:int(plan["limit"])]

    # Step 5: Chart
    c = plan.get("chart")
    if c and table_rows:
        c_type = str(c.get("type", "bar")).lower().strip()
        cat_c = find_best_category_col(table_rows[0], c.get("cat_col") or (plan.get("group_by", [None])[0]))
        val_cs = find_best_value_cols(table_rows[0], cat_c, c.get("val_cols"))

        c_title = c.get("title") or "Comparison"
        if " by " in c_title:
            prefix = c_title.split(" by ")[0]
            c_title = f"{prefix} by {cat_c.replace('_', ' ').title()}"
        elif not c.get("title") and val_cs:
            c_title = f"{val_cs[0].replace('_', ' ').title()} by {cat_c.replace('_', ' ').title()}"

        if c_type in ("all", "multi", "all_types", "charts", "both"):
            b_chart = build_echarts(chart_type="bar", title=f"{c_title} (Bar)", data_rows=table_rows, cat_col=cat_c, val_cols=val_cs)
            d_chart = build_echarts(chart_type="donut", title=f"{c_title} (Share)", data_rows=table_rows, cat_col=cat_c, val_cols=val_cs[:1])
            multi_list = [ch for ch in [b_chart, d_chart] if ch]
            chart_obj = multi_list if len(multi_list) > 1 else (multi_list[0] if multi_list else None)
        else:
            chart_obj = build_echarts(
                chart_type=c_type,
                title=c_title,
                data_rows=table_rows,
                cat_col=cat_c,
                val_cols=val_cs,
                subtext=c.get("subtext", "")
            )

    # Auto-generate chart if query is aggregate with multiple categories, or if user asked for a chart
    if not chart_obj and table_rows and len(table_rows) >= 2:
        # Avoid unreadable 30+ bar charts on raw data dumps unless explicitly requested in plan
        can_auto_chart = (mode in ("aggregate", "pivot")) or (len(table_rows) <= 12)
        if "chart" in plan and plan.get("chart") is None:
            can_auto_chart = False
        elif plan.get("chart"):
            can_auto_chart = True

        if can_auto_chart:
            cat_c = find_best_category_col(table_rows[0], (plan.get("group_by") or [None])[0])
            val_cs = find_best_value_cols(table_rows[0], cat_c)
            if val_cs:
                chart_obj = build_echarts(
                    chart_type="donut" if len(table_rows) <= 6 else "bar",
                    title=f"{val_cs[0].replace('_', ' ').title()} by {str(cat_c).replace('_', ' ').title()}",
                    data_rows=table_rows,
                    cat_col=str(cat_c),
                    val_cols=val_cs[:2]
                )

    # Step 6: KPIs
    kpis = compute_kpis(table_rows if plan.get("limit") else (rows if mode == "raw" else table_rows))

    return {
        "table_rows": table_rows,
        "table_md": rows_to_md_table(table_rows),
        "kpis": kpis,
        "chart": chart_obj,
        "row_count": len(table_rows),
        "source_rows": len(rows),
        "mode": mode,
    }


# ---------------------------------------------------------------------------
# Multi-Table Joins & In-Memory Analytical Engine (DuckDB & Vectorization)
# ---------------------------------------------------------------------------

def join_datasets(
    left_rows: List[dict],
    right_rows: List[dict],
    left_on: Union[str, List[str]],
    right_on: Union[str, List[str]],
    how: str = "inner",
    left_name: str = "left",
    right_name: str = "right"
) -> List[dict]:
    """
    High-performance in-memory join between two datasets using DuckDB (if installed)
    or an optimized pure-Python hash join.
    """
    if not left_rows:
        return []
    if not right_rows:
        return left_rows if how.lower() in ("left", "outer") else []

    l_cols = [left_on] if isinstance(left_on, str) else list(left_on)
    r_cols = [right_on] if isinstance(right_on, str) else list(right_on)

    if HAS_DUCKDB:
        try:
            con = duckdb.connect(database=":memory:")
            ldf = pd.DataFrame(left_rows)
            rdf = pd.DataFrame(right_rows)

            t_left = re.sub(r"\W+", "_", left_name.strip()).lower().strip("_") or "t_left"
            t_right = re.sub(r"\W+", "_", right_name.strip()).lower().strip("_") or "t_right"
            if t_left == t_right:
                t_right = f"{t_right}_2"

            con.register(t_left, ldf)
            con.register(t_right, rdf)

            l_actual = {c.lower(): c for c in ldf.columns}
            r_actual = {c.lower(): c for c in rdf.columns}

            join_conds = []
            for lc, rc in zip(l_cols, r_cols):
                real_lc = l_actual.get(lc.lower(), lc)
                real_rc = r_actual.get(rc.lower(), rc)
                join_conds.append(f'"{t_left}"."{real_lc}" = "{t_right}"."{real_rc}"')

            on_clause = " AND ".join(join_conds)
            join_type = how.upper() if how.upper() in ("INNER", "LEFT", "RIGHT", "FULL") else "INNER"
            if join_type == "OUTER":
                join_type = "FULL"

            shared = set(ldf.columns).intersection(set(rdf.columns))
            select_parts = [f'"{t_left}"."{c}"' for c in ldf.columns]
            for c in rdf.columns:
                if c in shared:
                    select_parts.append(f'"{t_right}"."{c}" AS "{t_right}_{c}"')
                else:
                    select_parts.append(f'"{t_right}"."{c}"')

            query = f'SELECT {", ".join(select_parts)} FROM "{t_left}" {join_type} JOIN "{t_right}" ON {on_clause}'
            res_df = con.execute(query).df()
            res_df = res_df.where(pd.notnull(res_df), None)
            return res_df.to_dict(orient="records")
        except Exception as e:
            pass

    # Pure Python Hash Join fallback
    l_key = l_cols[0].lower()
    r_key = r_cols[0].lower()
    r_index: Dict[Any, List[dict]] = {}
    for r in right_rows:
        val = None
        for k, v in r.items():
            if k.lower() == r_key:
                val = str(v).strip().lower() if v is not None else None
                break
        if val is not None:
            r_index.setdefault(val, []).append(r)

    joined = []
    for l in left_rows:
        val = None
        for k, v in l.items():
            if k.lower() == l_key:
                val = str(v).strip().lower() if v is not None else None
                break
        matches = r_index.get(val, [])
        if matches:
            for m in matches:
                merged = dict(l)
                for mk, mv in m.items():
                    if mk in merged:
                        merged[f"{right_name}_{mk}"] = mv
                    else:
                        merged[mk] = mv
                joined.append(merged)
        elif how.lower() in ("left", "outer"):
            joined.append(dict(l))

    return joined


def execute_multi_table_plan(datasets: Dict[str, List[dict]], plan: dict) -> dict:
    """
    Executes a multi-table analytical plan (cross-table JOIN or combined breakdown)
    over live datasets in-memory using DuckDB or vectorization.
    """
    left_table = plan.get("left_table") or plan.get("target_table")
    right_table = plan.get("right_table")

    if not right_table and plan.get("target_tables") and len(plan["target_tables"]) >= 2:
        left_table = plan["target_tables"][0]
        right_table = plan["target_tables"][1]

    left_rows = datasets.get(left_table) or []
    right_rows = datasets.get(right_table) or []

    # Case-insensitive table matching
    if not left_rows and left_table:
        for k in datasets:
            if k.lower() == str(left_table).lower():
                left_rows = datasets[k]
                left_table = k
                break
    if not right_rows and right_table:
        for k in datasets:
            if k.lower() == str(right_table).lower():
                right_rows = datasets[k]
                right_table = k
                break

    if not right_rows:
        return execute_plan(left_rows, plan)
    if not left_rows:
        return execute_plan(right_rows, plan)

    join_on = plan.get("join_on") or plan.get("on")
    if not join_on:
        l_cols = {c.lower(): c for c in left_rows[0].keys()} if left_rows else {}
        r_cols = {c.lower(): c for c in right_rows[0].keys()} if right_rows else {}
        common = set(l_cols.keys()).intersection(set(r_cols.keys()))
        prio_keys = [c for c in common if any(k in c for k in ("id", "code", "no", "key", "dept", "supplier", "product", "item"))]
        join_on = prio_keys[0] if prio_keys else (list(common)[0] if common else None)

    if not join_on:
        joined_rows = left_rows + right_rows
    else:
        how = plan.get("how", "left" if plan.get("mode") == "join" else "inner")
        joined_rows = join_datasets(
            left_rows,
            right_rows,
            left_on=join_on,
            right_on=join_on,
            how=how,
            left_name=str(left_table),
            right_name=str(right_table)
        )

    return execute_plan(joined_rows, plan)


def execute_duckdb_query(datasets: Dict[str, List[dict]], sql_query: str) -> List[dict]:
    """Executes safe analytical SQL directly over in-memory datasets using DuckDB."""
    if not HAS_DUCKDB:
        return []
    try:
        con = duckdb.connect(database=":memory:")
        for t_name, rows in datasets.items():
            if rows:
                df = pd.DataFrame(rows)
                safe_name = re.sub(r"\W+", "_", t_name.strip()).lower().strip("_")
                con.register(safe_name, df)
                con.register(t_name, df)
        res_df = con.execute(sql_query).df()
        res_df = res_df.where(pd.notnull(res_df), None)
        return res_df.to_dict(orient="records")
    except Exception as e:
        print(f"[DuckDB Query Error] {e}")
        return []


def extract_table_profiles(rows: List[dict], max_distinct: int = 15) -> Dict[str, List[str]]:
    """
    Profiles table columns and extracts top distinct values for low-cardinality categorical columns.
    E.g.: {'status': ['IN_STOCK', 'ON_LEAVE', 'ACTIVE'], 'department': ['Human Resources', 'IT', 'Finance']}
    """
    if not rows:
        return {}
    sample = rows[:500]
    profiles = {}
    col_values: Dict[str, set] = {}
    for r in sample:
        for k, v in r.items():
            if v is not None and str(v).strip():
                col_values.setdefault(k, set()).add(str(v).strip())

    for col, vals in col_values.items():
        if 1 < len(vals) <= max_distinct:
            if not all(str(x).isdigit() for x in vals):
                profiles[col] = sorted(list(vals))[:max_distinct]
    return profiles
