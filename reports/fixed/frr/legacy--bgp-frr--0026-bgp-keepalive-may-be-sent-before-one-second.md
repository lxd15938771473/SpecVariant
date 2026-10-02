# BGP KEEPALIVE May Be Sent Before One Second

## Analysis
Reproduction condition: BGP KEEPALIVE send interval

FRR can send periodic BGP KEEPALIVE before a full one-second interval has elapsed when the effective keepalive interval is one second.

## Standard Requirement

Official standard: [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html)

[RFC 4271 Section 4.2](https://www.rfc-editor.org/rfc/rfc4271.html#section-4.2) allows Hold Time to be zero or at least three seconds:

```text
The Hold Time MUST be either zero or at least three seconds.
```

[RFC 4271 Section 4.4](https://www.rfc-editor.org/rfc/rfc4271.html#section-4.4) sets a hard lower bound on KEEPALIVE spacing:

```text
KEEPALIVE messages MUST NOT be sent more
frequently than one per second.
```

So a negotiated Hold Time of 3 seconds is valid, and it naturally gives a one-second KEEPALIVE interval. Even in that case, KEEPALIVE messages must not be sent less than one second apart.

## Relevant Source Code

`frr-master/bgpd/bgp_packet.c:2136-2179`

```c
if (holdtime < 3 && holdtime != 0) {
	bgp_notify_send_with_data(connection, BGP_NOTIFY_OPEN_ERR,
				  BGP_NOTIFY_OPEN_UNACEP_HOLDTIME,
				  notify_data_holdtime, 2);
	return BGP_Stop;
}

if (holdtime < send_holdtime)
	peer->v_holdtime = holdtime;
else
	peer->v_holdtime = send_holdtime;

peer->v_keepalive = peer->v_holdtime / 3;
if (CHECK_FLAG(peer->flags, PEER_FLAG_TIMER)) {
	if (peer->keepalive && peer->keepalive < peer->v_keepalive)
		peer->v_keepalive = peer->keepalive;
} else {
	if (peer->bgp->default_keepalive
	    && peer->bgp->default_keepalive < peer->v_keepalive)
		peer->v_keepalive = peer->bgp->default_keepalive;
}
```

This accepts `holdtime=3` and sets `v_keepalive=1`.

`frr-master/bgpd/bgp_keepalives.c:87-112`

```c
static const struct timeval tolerance = {0, 100000};

uint32_t v_ka = atomic_load_explicit(&pkat->peer->v_keepalive,
				     memory_order_relaxed);

if (v_ka == 0)
	return;

monotime_since(&pkat->last, &elapsed);

ka.tv_sec = v_ka;
timersub(&ka, &elapsed, &diff);

int send_keepalive =
	elapsed.tv_sec >= ka.tv_sec || timercmp(&diff, &tolerance, <);

if (send_keepalive) {
	bgp_keepalive_send(pkat->peer->connection);
	monotime(&pkat->last);
```

For `v_keepalive=1`, this sends when less than 100 ms remains before the one-second mark. That permits a send at about `0.900001s`.

`frr-master/bgpd/bgp_keepalives.c:247-259`

```c
frr_with_mutex (peerhash_mtx) {
	if (CHECK_FLAG(peer->thread_flags, PEER_THREAD_KEEPALIVES_ON))
		return;

	holder.peer = peer;
	if (!hash_lookup(peerhash, &holder)) {
		struct pkat *pkat = pkat_new(peer);
		(void)hash_get(peerhash, pkat, hash_alloc_intern);
		peer_lock(peer);
	}
	SET_FLAG(peer->thread_flags, PEER_THREAD_KEEPALIVES_ON);
	pthread_cond_signal(peerhash_cond);
}
```

The keepalive worker can be woken before the next timed wait expires, so the early-send condition is reachable when the peer is processed inside the 100 ms tolerance window.

## Runtime Evidence

A focused executable model evaluated FRR's keepalive scheduling condition with negotiated Hold Time 3 seconds and keepalive interval 1 second. It swept elapsed times across the 0.9-second tolerance boundary. The model did not send at 0.900000 seconds but did send at 0.900001, 0.950000, and 0.999999 seconds. This demonstrates the early-send branch of the copied timing predicate; it is not a measurement of transmitted KEEPALIVE packets from bgpd.

Recorded output and checks (local artifact paths omitted):

```text
holdtime=3 -> v_keepalive=1
elapsed=0.899999s diff=0.100001s send=False
elapsed=0.900000s diff=0.100000s send=False
elapsed=0.900001s diff=0.099999s send=True
elapsed=0.950000s diff=0.050000s send=True
elapsed=0.999999s diff=0.000001s send=True
elapsed=1.000000s diff=0.000000s send=True
```

## Inconsistency Reason

The standard forbids sending KEEPALIVE more frequently than once per second. FRR allows `v_keepalive=1`, then treats an elapsed time slightly above `0.9s` as ready because of the 100 ms tolerance. That can produce KEEPALIVE spacing below one second, so the implementation is inconsistent with [RFC 4271 Section 4.4](https://www.rfc-editor.org/rfc/rfc4271.html#section-4.4).

## Fix Direction

Do not apply the early-send tolerance when `v_keepalive <= 1`, or clamp the send decision so periodic KEEPALIVE is never emitted until at least one full second has elapsed since the previous KEEPALIVE.
