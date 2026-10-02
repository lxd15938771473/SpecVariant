# FRR oldest-eBGP rule can bypass RFC tie-breakers

## Summary

This report addresses the [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) Phase 2 tie-break rules for the lowest BGP Identifier and, when BGP Identifiers are equal, the lowest peer address.

FRR can return earlier in the default eBGP path comparison: if both paths are eBGP and one is already selected, `bgp_path_info_cmp()` keeps the older selected path before doing Router-ID or peer-address comparison. Therefore a previously selected route can beat a newer route that should win under the RFC tie-break sequence.

## Standard Requirement

Official standard: [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html), Section 9.1.2.2, "Breaking Ties (Phase 2)".

```text
The tie-breaking algorithm begins by considering all equally
preferable routes to the same destination, and then selects routes to
be removed from consideration.  The algorithm terminates as soon as
only one route remains in consideration.  The criteria MUST be
applied in the order specified.
```

```text
f) Remove from consideration all routes other than the route that
   was advertised by the BGP speaker with the lowest BGP
   Identifier value.

g) Prefer the route received from the lowest peer address.
```

Interpretation: after earlier criteria still leave multiple candidate routes, step f must decide by the lowest BGP Identifier before step g or any later local tie-breaker. An implementation may use different internal code only if it produces the same result.

## Relevant Source Code

`bgpd/bgp_route.c:1776-1799`

```c
/* 12. If both paths are external, prefer the path that was received
   first (the oldest one).  This step minimizes route-flap, since a
   newer path won't displace an older one, even if it was the
   preferred route based on the additional decision criteria below.  */
if (!CHECK_FLAG(bgp->flags, BGP_FLAG_COMPARE_ROUTER_ID)
    && new_sort == BGP_PEER_EBGP && exist_sort == BGP_PEER_EBGP) {
	if (CHECK_FLAG(new->flags, BGP_PATH_SELECTED)) {
		*reason = bgp_path_selection_older;
		return 1;
	}

	if (CHECK_FLAG(exist->flags, BGP_PATH_SELECTED)) {
		*reason = bgp_path_selection_older;
		return 0;
	}
}
```

`bgpd/bgp_route.c:1801-1828`

```c
/* 13. Router-ID comparison. */
if (bgp_attr_exists(new_path_for_modifiable_attr->attr, BGP_ATTR_ORIGINATOR_ID))
	new_id.s_addr = new_path_for_modifiable_attr->attr->originator_id.s_addr;
else
	new_id.s_addr = peer_new->remote_id.s_addr;

if (bgp_attr_exists(exist_path_for_modifiable_attr->attr, BGP_ATTR_ORIGINATOR_ID))
	exist_id.s_addr = exist_path_for_modifiable_attr->attr->originator_id.s_addr;
else
	exist_id.s_addr = peer_exist->remote_id.s_addr;

if (ntohl(new_id.s_addr) < ntohl(exist_id.s_addr)) {
	*reason = bgp_path_selection_router_id;
	return 1;
}

if (ntohl(new_id.s_addr) > ntohl(exist_id.s_addr)) {
	*reason = bgp_path_selection_router_id;
	return 0;
}
```

`bgpd/bgp_vty.c:4452-4477`

```c
/* "bgp bestpath compare-routerid" configuration.  */
DEFUN (bgp_bestpath_compare_router_id,
       bgp_bestpath_compare_router_id_cmd,
       "bgp bestpath compare-routerid",
       BGP_STR
       "Change the default bestpath selection\n"
       "Compare router-id for identical EBGP paths\n")
{
	VTY_DECLVAR_CONTEXT(bgp, bgp);
	SET_FLAG(bgp->flags, BGP_FLAG_COMPARE_ROUTER_ID);
	bgp_recalculate_all_bestpaths(bgp);

	return CMD_SUCCESS;
}
```

The Router-ID comparison is present, but default behavior reaches it only after the `oldest external` branch fails or `bgp bestpath compare-routerid` is enabled.

## Implementation Behavior

Default FRR behavior:

1. Earlier bestpath criteria can leave two equal eBGP routes under consideration.
2. Before Router-ID comparison, FRR checks whether one eBGP path is already selected.
3. If so, FRR returns that older selected path immediately.
4. The lower BGP Identifier or lower peer-address route is not considered unless `BGP_FLAG_COMPARE_ROUTER_ID` is set or the oldest-eBGP branch does not decide.

## Inconsistency Reason

[RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) requires tie-break criteria to be applied in order: step f keeps the route from the lowest BGP Identifier, and step g keeps the route from the lowest peer address when BGP Identifiers match. FRR's default path can decide by "oldest external" before those comparisons. That can produce a different result from [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) when the older selected eBGP path should lose under either later tie-break.

This is configuration-dependent, not absent code: `bgp bestpath compare-routerid` can enable the Router-ID-first behavior for identical eBGP paths, but the default algorithm can still violate the RFC step order.

## Runtime Evidence

Four candidate controls and source reproducers returned ok=true for baseline, state-order, boundary, and unknown cases. A focused logic witness compared equally preferable routes at the relevant tie-break stage: the RFC BGP-Identifier rule selected 1.1.1.1, while the modeled default oldest-eBGP branch selected 2.2.2.2. No daemon-level selection was observed. bgpd/zebra/vtysh were missing, and the bestpath-reason topotest failed on import of resource.

Recorded output and checks (local artifact paths omitted):

```text
control:    ok=true, source snapshot and RFC/code excerpts readable
reproducer: ok=true
risk_class: baseline, state_order, boundary, unknown
```

```text
RFC step f winner: 1.1.1.1
FRR default old-eBGP branch winner: 2.2.2.2
```

```text
bgpd=NOT_FOUND
zebra=NOT_FOUND
vtysh=NOT_FOUND
bgpd/bgpd.exe=False
zebra/zebra.exe=False
vtysh/vtysh.exe=False
ImportError: tests/topotests/conftest.py imports Python module resource, which is unavailable here
```

## Impact

Bestpath selection can become arrival-order dependent for tied eBGP routes. A route from a lower BGP Identifier or lower peer address may fail to replace an older selected eBGP route, contrary to [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) Phase 2 tie-break ordering.

## Fix Direction

For [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html)-compliant default behavior, apply Router-ID and peer-address comparison before the oldest-eBGP preference when Phase 2 reaches these tie-break stages, or make the default produce the same result as steps f and g. If FRR intentionally keeps the oldest-eBGP policy as a compatibility mode, document it as non-RFC-default behavior and keep the RFC behavior available through configuration.
