# Partial bit is cleared on recognized optional transitive attributes

## Summary

[RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) requires an AS that accepts and forwards a recognized optional transitive attribute with `Partial=1` to preserve that bit. FRR's inbound flag validation permits it, but parsing retains the typed attribute value and presence bit rather than the original flags. Outbound serialization rebuilds fixed flags; for example, COMMUNITY uses Optional|Transitive, with Extended-Length when needed, without restoring the received Partial bit.

## Standard Requirement

Official standard: [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html), Section 5, Path Attributes

[RFC 4271 Section 5](https://www.rfc-editor.org/rfc/rfc4271.html#section-5)

```text
If a path with a recognized, transitive optional attribute
is accepted and passed along to other BGP peers and the Partial bit
in the Attribute Flags octet is set to 1 by some previous AS, it MUST
NOT be set back to 0 by the current AS.
```

When the stated conditions apply, Partial=1 is a wire flag that must survive forwarding.

## Relevant Source Code

`implementions/frr-master/frr-master/bgpd/bgp_attr.c:2003-2105`

```c
/* Required flags for attributes. EXTLEN will be masked off when testing,
 * as will PARTIAL for optional+transitive attributes.
 */
const uint8_t attr_flags_values[] = {
	[BGP_ATTR_COMMUNITIES] = BGP_ATTR_FLAG_TRANS | BGP_ATTR_FLAG_OPTIONAL,
	[BGP_ATTR_EXT_COMMUNITIES] =
		BGP_ATTR_FLAG_OPTIONAL | BGP_ATTR_FLAG_TRANS,
	[BGP_ATTR_LARGE_COMMUNITIES] =
		BGP_ATTR_FLAG_OPTIONAL | BGP_ATTR_FLAG_TRANS,
	[BGP_ATTR_OTC] = BGP_ATTR_FLAG_OPTIONAL | BGP_ATTR_FLAG_TRANS
};

static bool bgp_attr_flag_invalid(struct bgp_attr_parser_args *args)
{
	uint8_t mask = BGP_ATTR_FLAG_EXTLEN;
	const uint8_t flags = args->flags;

	if (CHECK_FLAG(flags, BGP_ATTR_FLAG_PARTIAL)) {
		if (!CHECK_FLAG(flags, BGP_ATTR_FLAG_OPTIONAL))
			return true;
		if (CHECK_FLAG(flags, BGP_ATTR_FLAG_OPTIONAL)
		    && !CHECK_FLAG(flags, BGP_ATTR_FLAG_TRANS))
			return true;
	}

	if (CHECK_FLAG(flags, BGP_ATTR_FLAG_OPTIONAL)
	    && CHECK_FLAG(flags, BGP_ATTR_FLAG_TRANS))
		SET_FLAG(mask, BGP_ATTR_FLAG_PARTIAL);

	if (CHECK_FLAG(flags, ~mask) == attr_flags_values[attr_code])
		return false;
}
```

Inbound validation includes Partial in the allowed mask for optional transitive attributes, so recognized attributes such as COMMUNITY can pass with Partial=1.

`implementions/frr-master/frr-master/bgpd/bgp_attr.c:2656-2688`

```c
static enum bgp_attr_parse_ret
bgp_attr_community(struct bgp_attr_parser_args *args)
{
	struct attr *const attr = args->attr;
	const bgp_size_t length = args->length;

	bgp_attr_set_community(attr,
			       community_parse((uint32_t *)stream_pnt(connection->curr), length));
	stream_forward_getp(connection->curr, length);

	return BGP_ATTR_PARSE_PROCEED;
}
```

`implementions/frr-master/frr-master/bgpd/bgp_attr.h:619-627`

```c
static inline void bgp_attr_set_community(struct attr *attr,
					  struct community *comm)
{
	attr->community = comm;

	if (comm)
		bgp_attr_set(attr, BGP_ATTR_COMMUNITIES);
	else
		bgp_attr_unset(attr, BGP_ATTR_COMMUNITIES);
}
```

Parsing saves the community value and the `BGP_ATTR_COMMUNITIES` presence bit, but does not retain the received flags or Partial state here.

`implementions/frr-master/frr-master/bgpd/bgp_attr.c:5808-5827`

```c
if (CHECK_FLAG(peer->af_flags[afi][safi], PEER_FLAG_SEND_COMMUNITY) &&
    bgp_attr_exists(attr, BGP_ATTR_COMMUNITIES)) {
	comm = bgp_attr_get_community(attr);
	if (comm->size * 4 > 255) {
		stream_putc(s,
			    BGP_ATTR_FLAG_OPTIONAL | BGP_ATTR_FLAG_TRANS
				    | BGP_ATTR_FLAG_EXTLEN);
		stream_putc(s, BGP_ATTR_COMMUNITIES);
		stream_putw(s, comm->size * 4);
	} else {
		stream_putc(s,
			    BGP_ATTR_FLAG_OPTIONAL
				    | BGP_ATTR_FLAG_TRANS);
		stream_putc(s, BGP_ATTR_COMMUNITIES);
		stream_putc(s, comm->size * 4);
	}
	stream_put(s, comm->val, comm->size * 4);
}
```

The outbound COMMUNITY writer rebuilds the flags as `0xc0` for short attributes or `0xd0` for extended-length attributes. Neither includes Partial (`0x20`).

`implementions/frr-master/frr-master/bgpd/bgp_attr.c:5528-5542`

```c
if (ecomm->size * ecomm->unit_size > 255) {
	stream_putc(s, BGP_ATTR_FLAG_OPTIONAL | BGP_ATTR_FLAG_TRANS | BGP_ATTR_FLAG_EXTLEN);
} else {
	stream_putc(s, BGP_ATTR_FLAG_OPTIONAL | BGP_ATTR_FLAG_TRANS);
}
```

The EXT_COMMUNITIES writer likewise uses fixed flags without preserving Partial.

## Implementation Behavior

For a recognized optional transitive COMMUNITY attribute:

1. Inbound Optional|Transitive|Partial (`0xe0`) passes flag validation.
2. The parser saves only the community value and attribute-presence state.
3. The outbound writer emits `0xc0` or `0xd0` according to length.
4. The original Partial=1 is lost, contrary to the [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) preservation requirement.

## Runtime Evidence

A focused C probe was compiled with `gcc -Wall -Wextra -O0` and executed to model optional-transitive flag validation and COMMUNITY output-flag construction. It supplied inbound flags 0xe0, which include Partial. The model accepted those flags but emitted 0xc0 for short COMMUNITY and 0xd0 for extended-length COMMUNITY, clearing Partial in both cases. The source-observation control and reproducer also passed. This is executable branch-model evidence, not a daemon forwarding test.

Recorded output and checks (local artifact paths omitted):

```text
inbound flags=0xe0 accepted=yes
outbound community short flags=0xc0 partial=no
outbound community extlen flags=0xd0 partial=no
```

## Impact

When FRR accepts and forwards a recognized optional transitive attribute with Partial=1, the inspected writer can clear the bit in the forwarded UPDATE. Downstream peers could then incorrectly infer that the attribute did not traverse an AS that failed to recognize it.

## Fix Direction

Retain inbound Partial state for recognized optional transitive attributes and merge it into the corresponding outbound flags, or preserve the original flags during forwarding. Fixed-flag writers for COMMUNITY, EXT_COMMUNITIES, LARGE_COMMUNITIES, and OTC need consistent handling.
