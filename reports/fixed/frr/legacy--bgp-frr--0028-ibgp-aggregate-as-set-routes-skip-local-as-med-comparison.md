# IBGP aggregate AS_SET routes skip local-AS MED comparison

## Summary

Reproduction condition: config-gated; reachable when `AS_SET` routes are permitted, for example with `no bgp reject-as-sets`.

FRR correctly compares MED for empty AS_PATH local aggregate routes, but it does not treat an IBGP aggregate route whose AS_PATH begins with `AS_SET` as having local AS for `neighborAS`.

## Standard Requirement

- Standard: [RFC 4271 Section 9.1.2.2](https://www.rfc-editor.org/rfc/rfc4271.html#section-9.1.2.2), Breaking Ties (Phase 2)
- Link: [RFC 4271 Section 9.1.2.2](https://www.rfc-editor.org/rfc/rfc4271.html#section-9.1.2.2)
- Evidence: [RFC 4271 Section 9.1.2.2](https://www.rfc-editor.org/rfc/rfc4271.html#section-9.1.2.2)

```text
Similarly, neighborAS(n) is a function that returns the
         neighbor AS from which the route was received.  If the route is
         learned via IBGP, and the other IBGP speaker didn't originate
         the route, it is the neighbor AS from which the other IBGP
         speaker learned the route.  If the route is learned via IBGP,
         and the other IBGP speaker either (a) originated the route, or
         (b) created the route by aggregation and the AS_PATH attribute
         of the aggregate route is either empty or begins with an
         AS_SET, it is the local AS.
```

Meaning: if two IBGP-learned aggregate routes both have empty AS_PATH or AS_PATH beginning with `AS_SET`, their `neighborAS` is the local AS, so they should be eligible for same-neighbor-AS MED comparison.

## Relevant Source Code

`bgpd/bgp_route.c:1514-1529`

```c
/* 6. MED check. */
internal_as_route = (aspath_count_hops(new_path_for_modifiable_attr->attr->aspath) == 0 &&
		     aspath_count_hops(exist_path_for_modifiable_attr->attr->aspath) == 0);

if (CHECK_FLAG(bgp->flags, BGP_FLAG_ALWAYS_COMPARE_MED) ||
    (CHECK_FLAG(bgp->flags, BGP_FLAG_MED_CONFED) && confed_as_route) ||
    aspath_cmp_left(new_path_for_modifiable_attr->attr->aspath,
		    exist_path_for_modifiable_attr->attr->aspath) ||
    aspath_cmp_left_confed(new_path_for_modifiable_attr->attr->aspath,
			   exist_path_for_modifiable_attr->attr->aspath) ||
    internal_as_route) {
	new_med = bgp_med_value(new_path_for_modifiable_attr->attr, bgp);
```

MED is compared only when one of these gates is true. The local/internal branch requires both AS_PATH hop counts to be zero.

`bgpd/bgp_aspath.c:419-423`

```c
while (seg) {
	if (seg->type == AS_SEQUENCE)
		count += seg->length;
	else if (seg->type == AS_SET)
		count++;
```

An `AS_SET` path is not hop-count-zero, so it misses `internal_as_route`.

`bgpd/bgp_aspath.c:1884-1901`

```c
if (!seg1 && !seg2)
	return true;

/* find first non-confed segments for each */
while (seg1 && ((seg1->type == AS_CONFED_SEQUENCE)
		|| (seg1->type == AS_CONFED_SET)))
	seg1 = seg1->next;

while (seg2 && ((seg2->type == AS_CONFED_SEQUENCE)
		|| (seg2->type == AS_CONFED_SET)))
	seg2 = seg2->next;

if (!(seg1 && seg2 && (seg1->type == AS_SEQUENCE)
      && (seg2->type == AS_SEQUENCE)))
	return false;
```

`aspath_cmp_left()` handles both empty paths and matching `AS_SEQUENCE` leftmost AS values, but rejects paths beginning with `AS_SET`.

`bgpd/bgp_aspath.c:1119-1150`

```c
/* Make as-set using rest of all information. */
...
aspath_add_asns_rightmost(aspath, seg1->as[i], AS_SET, 1);
...
aspath_add_asns_rightmost(aspath, seg2->as[i], AS_SET, 1);
...
aspath->count = aspath_count_hops_internal(aspath);
```

FRR's aggregate AS_PATH construction can create `AS_SET` segments.

`bgpd/bgp_route.c:11690-11751`

```c
"[no] aggregate-address <A.B.C.D/M$prefix|A.B.C.D$addr A.B.C.D$mask> [{"
"as-set$as_set_s"
...
if (as_set_s)
	as_set = AGGREGATE_AS_SET;
```

The `aggregate-address ... as-set` CLI exposes this route shape.

`bgpd/bgpd.c:4086`, `bgpd/bgp_vty.c:3340-3350`

```c
bgp->reject_as_sets = true;

DEFUN(no_bgp_reject_as_sets, no_bgp_reject_as_sets_cmd,
      "no bgp reject-as-sets",
...
	bgp->reject_as_sets = false;
```

Default FRR limits AS_SET propagation, but configuration can allow AS_SET paths.

## Implementation Behavior

For empty AS_PATH aggregate routes, both `aspath_count_hops()` values are zero, so `internal_as_route` enables MED comparison.

For aggregate routes beginning with `AS_SET`, `aspath_count_hops()` returns 1 and `aspath_cmp_left()` returns false because the first non-confed segment is not `AS_SEQUENCE`. Unless `bgp always-compare-med` is configured, FRR skips MED comparison even though [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) defines `neighborAS` as local AS.

## Inconsistency Reason

[RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) groups IBGP aggregate routes with empty AS_PATH and with AS_PATH beginning with `AS_SET` under local-AS `neighborAS`. FRR implements the empty AS_PATH case, but its default MED gate does not include the `AS_SET` case. This is a partial implementation gap, not a total MED failure.

## Runtime Evidence

A focused executable model evaluated the exact inspected MED-gating predicates for three pairs: empty aggregate paths, AS_SET aggregate paths, and paths with the same leftmost AS_SEQUENCE. The empty-path and same-sequence controls enabled comparison. The AS_SET aggregate pair did not, although the stated requirement treats both neighboring ASes as local. The checker intentionally exited 1 for that mismatch and produced empty stderr. No local bgpd or vtysh was available; these are predicate-model results, not a live route-selection test.

Recorded output and checks (local artifact paths omitted):

| Case | RFC requires MED gate | FRR MED gate | Result |
| --- | --- | --- | --- |
| empty aggregate vs empty aggregate | true | true | pass |
| AS_SET aggregate vs AS_SET aggregate | true | false | fail |
| same leftmost AS_SEQUENCE | true | true | pass |

## Impact

When AS_SET routes are allowed, FRR can choose between otherwise tied IBGP aggregate routes without applying the RFC-required local-AS MED comparison. That can select a higher-MED path and produce non-RFC bestpath behavior.

## Fix Direction

In the MED gate, treat an IBGP aggregate route whose AS_PATH is empty or whose first non-confed segment is `AS_SET` as local-AS for `neighborAS`. Keep the existing empty AS_PATH behavior and preserve `always-compare-med` as a broader override.
