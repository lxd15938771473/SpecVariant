# NEXT_HOP semantic error logging

## Standard

[RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) defines a NEXT_HOP as semantically incorrect when it is the receiver's own address, or when an EBGP NEXT_HOP is neither the sender address nor on a shared subnet. Such an error should be logged and the route should be ignored.

[RFC 4271 Section 6.3](https://www.rfc-editor.org/rfc/rfc4271.html#section-6.3)

```text
The IP address in the NEXT_HOP MUST meet the following criteria to be
considered semantically correct:

   a) It MUST NOT be the IP address of the receiving speaker.

   b) In the case of an EBGP, where the sender and receiver are one
      IP hop away from each other, either the IP address in the
      NEXT_HOP MUST be the sender's IP address that is used to
      establish the BGP connection, or the interface associated with
      the NEXT_HOP IP address MUST share a common subnet with the
      receiving BGP speaker.

If the NEXT_HOP attribute is semantically incorrect, the error SHOULD
be logged, and the route SHOULD be ignored.  In this case, a
NOTIFICATION message SHOULD NOT be sent, and the connection SHOULD
NOT be closed.
```

The extracted requirement is:

```json
{
"condition": "The NEXT_HOP attribute is semantically incorrect.",
"error_behavior": "SHOULD log the error.",
"check_type": "error_handling",
"why_checkable": "A semantic NEXT_HOP failure can be correlated with local logging."
}
```

## Code

FRR detects self NEXT_HOP as invalid. `bgp_update_martian_nexthop()` checks `bgp_nexthop_self()` for both `NEXT_HOP` and `MP_NEXTHOP`.

`implementions/frr-master/frr-master/bgpd/bgp_route.c:5764-5838`

```c
if (bgp_attr_exists(attr, BGP_ATTR_NEXT_HOP))
	nh_invalid = (attr->nexthop.s_addr == INADDR_ANY ||
		      !ipv4_unicast_valid(&attr->nexthop) ||
		      bgp_nexthop_self(bgp, afi, type, stype, attr, dest));
...
return nh_invalid;
```

`bgp_nexthop_self()` compares the received next hop against the local BGP address hashes.

`implementions/frr-master/frr-master/bgpd/bgp_nexthop.c:518-570`

```c
if (bgp_attr_exists(attr, BGP_ATTR_NEXT_HOP)) {
	tmp_addr.p.u.prefix4 = attr->nexthop;
	tmp_addr.p.prefixlen = IPV4_MAX_BITLEN;
}
...
addr = hash_lookup(bgp->address_hash, &tmp_addr);
if (addr)
	return true;
```

The route is ignored when this check fails, but the receive path only logs the reason under update debugging.

`implementions/frr-master/frr-master/bgpd/bgp_route.c:6417-6423`

```c
if (!CHECK_FLAG(peer->flags, PEER_FLAG_IS_RFAPI_HD) &&
    bgp_update_martian_nexthop(bgp, afi, safi, type, sub_type,
			       &new_attr, dest)) {
	peer->stat_pfx_nh_invalid++;
	reason = "martian or self next-hop;";
	bgp_attr_flush(&new_attr);
	goto filtered;
}
```

`implementions/frr-master/frr-master/bgpd/bgp_route.c:7066-7076`

```c
if (bgp_debug_update(peer, p, NULL, 1)) {
	...
	zlog_debug("%pBP rcvd UPDATE about %s -- DENIED due to: %s",
		   peer, pfx_buf, reason);
}
```

The NHT path has the same issue: the visible log is debug-only.

`implementions/frr-master/frr-master/bgpd/bgp_nht.c:1537-1543`

```c
if (bgp_update_martian_nexthop(
	    bnc->bgp, afi, safi, path->type,
	    path->sub_type, path->attr, dest)) {
	if (BGP_DEBUG(nht, NHT))
		zlog_debug(
			"%s: prefix %pBD (vrf %s), ignoring path due to martian or self-next-hop",
			__func__, dest, bgp_path->name);
}
```

This is not a blanket claim about all NEXT_HOP errors. FRR does log some martian NEXT_HOP cases by default.

`implementions/frr-master/frr-master/bgpd/bgp_attr.c:2314-2318`

```c
if (ipv4_martian(&attr->nexthop) && !bgp->allow_martian) {
	flog_err(EC_BGP_ATTR_MARTIAN_NH, "Martian nexthop %pI4",
		 &attr->nexthop);

	return BGP_ATTR_PARSE_WITHDRAW;
}
```

`implementions/frr-master/frr-master/bgpd/bgp_attr.c:2873-2876`

```c
if (ipv4_martian(&attr->mp_nexthop_global_in) && !peer->bgp->allow_martian) {
	zlog_warn("%s sent martian nexthop %pI4 in MP_REACH_NLRI", peer->host,
		  &attr->mp_nexthop_global_in);
	return BGP_ATTR_PARSE_WITHDRAW;
}
```

## Runtime Evidence

Four candidate controls and source reproducers returned ok=true. A focused source-path scan then counted logging calls in the self-next-hop rejection branches. The route-filter block contained zero non-debug and four debug calls; the NHT self-next-hop block contained zero non-debug and two debug calls. A martian-parser control contained two non-debug calls. The run observed source structure and logging guards, not emitted daemon log messages during a live routing failure.

Recorded output and checks (local artifact paths omitted):

```text
baseline: control ok=true, reproducer ok=true
replay: control ok=true, reproducer ok=true
error-mapping: control ok=true, reproducer ok=true
duplicate: control ok=true, reproducer ok=true
```

```text
self-next-hop semantic rejection is observable only under BGP_DEBUG(nht, NHT),
so default logging for that concrete branch is not closed.
```

```text
ROUTE_SELF_NH_FILTER_BLOCK_NONDEBUG_LOGS=0
ROUTE_SELF_NH_FILTER_BLOCK_DEBUG_LOGS=4
NHT_SELF_NH_BLOCK_NONDEBUG_LOGS=0
NHT_SELF_NH_BLOCK_DEBUG_LOGS=2
MARTIAN_PARSE_BLOCK_NONDEBUG_LOGS=2
```

## Decision

FRR ignores the self NEXT_HOP route, but the concrete self-next-hop rejection paths only expose debug logs, while [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) says semantic NEXT_HOP errors should be logged. The issue is limited in severity because the RFC uses `SHOULD`, and some other martian NEXT_HOP cases do have default logs.
