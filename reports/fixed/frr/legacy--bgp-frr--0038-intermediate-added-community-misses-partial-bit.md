# Intermediate-added COMMUNITY misses Partial bit

## Standard Requirement

[RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html), Section 5:

```text
New, transitive optional attributes MAY be attached to the path by
the originator or by any other BGP speaker in the path. If they are
not attached by the originator, the Partial bit in the Attribute
Flags octet is set to 1.
```

The same paragraph allows optional attributes to be updated by BGP speakers in the path. [RFC 1997](https://www.rfc-editor.org/rfc/rfc1997.html) makes this applicable to `COMMUNITIES`: it defines `COMMUNITIES` as optional transitive, and says a receiver may modify or append communities before propagation.

## Relevant Source Code

`bgpd/bgp_attr.h:31-33`:

```c
#define BGP_ATTR_FLAG_OPTIONAL  0x80
#define BGP_ATTR_FLAG_TRANS     0x40
#define BGP_ATTR_FLAG_PARTIAL   0x20
```

So `Optional|Transitive` is `0xc0`; `Optional|Transitive|Partial` is `0xe0`.

`bgpd/bgp_routemap.c:2888-2931` can newly add `COMMUNITIES`:

```c
if (rcs->additive && old) {
	merge = community_merge(community_dup(old), rcs->com);
	new = community_uniq_sort(merge);
	community_free(&merge);
} else
	new = community_dup(rcs->com);

bgp_attr_set_community(attr, new);
```

FRR has an existing intermediate-route scenario in `tests/topotests/bgp_community_change_update/y2/bgpd.conf:13-17`:

```text
neighbor 10.0.6.2 route-map z1 in
route-map z1 permit 10
  set community 65004:2
```

`bgpd/bgp_attr.c:5808-5827` serializes `COMMUNITIES` without `BGP_ATTR_FLAG_PARTIAL`:

```c
if (CHECK_FLAG(peer->af_flags[afi][safi], PEER_FLAG_SEND_COMMUNITY) &&
    bgp_attr_exists(attr, BGP_ATTR_COMMUNITIES)) {
	if (comm->size * 4 > 255)
		stream_putc(s, BGP_ATTR_FLAG_OPTIONAL | BGP_ATTR_FLAG_TRANS
			       | BGP_ATTR_FLAG_EXTLEN);
	else
		stream_putc(s, BGP_ATTR_FLAG_OPTIONAL | BGP_ATTR_FLAG_TRANS);
	stream_put(s, comm->val, comm->size * 4);
}
```

`struct attr` in `bgpd/bgp_attr.h:177-347` has no per-typed-attribute Partial/originator state. The unknown-attribute path does set Partial in `bgpd/bgp_attr.c:4310-4370`, but it stores raw unrecognized attributes in `attr->transit` and does not cover recognized `COMMUNITIES`.

## Inconsistency Reason

[RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) requires Partial=1 when a non-originator newly attaches a transitive optional attribute. FRR can add `COMMUNITIES` to a received route with route-map policy, but the outbound typed writer always emits `0xc0` or `0xd0`, not `0xe0` or `0xf0`.

`AGGREGATOR` is not used as the main proof because aggregation can form a new aggregate route. The concrete issue is recognized `COMMUNITIES` added on a route learned from another BGP speaker.

## Runtime Evidence

A focused source-path probe calculated optional-transitive flags and searched the attribute structure, COMMUNITY and LARGE_COMMUNITY writers, route-map community setter, and an intermediate-community topotest. It found 0xc0 for Optional|Transitive versus 0xe0 when Partial is included, no retained Partial state or corresponding writer/setter handling, and an existing intermediate-set-community test scenario. Live execution was blocked: the Docker runner could not identify a git repository root, Windows pytest lacked resource, and WSL lacked pytest. No forwarded packet was captured.

Recorded output and checks (local artifact paths omitted):

```json
{
  "ok": true,
  "computed_flags": {
    "optional_transitive": "0xc0",
    "optional_transitive_partial": "0xe0"
  },
  "attr_struct_has_partial_state": false,
  "community_writer_has_partial": false,
  "large_community_writer_has_partial": false,
  "route_set_community_mentions_partial": false,
  "topotest_has_intermediate_set_community": true
}
```

## Impact

Peers receive an intermediate-added optional transitive attribute as if it were complete/originator-attached, losing the [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) Partial-bit signal.

## Fix Direction

Track recognized optional transitive attributes newly attached on non-originated routes, and include `BGP_ATTR_FLAG_PARTIAL` when serializing those attributes.
