# Paho can send PINGREQ after the Keep Alive interval

## Problem Description

MQTT Keep Alive is a maximum interval between outgoing control packets. Paho's keepalive loop uses a coarse ticker and only sends PINGREQ on a tick after the elapsed time reaches the configured Keep Alive, so some values can be exceeded before the ping is sent.

## Summary

The tested configuration shows a client with KeepAlive 11 seconds sending PINGREQ after roughly 15 seconds.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 3.1.2.10 defines Keep Alive as the maximum permitted gap:

```text
The Keep Alive is a time interval measured in seconds. Expressed as a 16-bit word, it is the maximum time interval that is permitted to elapse between the point at which the Client finishes transmitting one Control Packet and the point it starts sending the next. It is the responsibility of the Client to ensure that the interval between Control Packets being sent does not exceed the Keep Alive value. In the absence of sending any other Control Packets, the Client MUST send a PINGREQ Packet [MQTT-3.1.2-23].
```

## Relevant Source Code

`client.go:633-658`

```go
if c.options.KeepAlive != 0 {
	atomic.StoreInt32(&c.pingOutstanding, 0)
	c.lastReceived.Store(time.Now())
	c.lastSent.Store(time.Now())
	c.workers.Add(1)
	go keepalive(c, conn)
}
```

The keepalive goroutine starts when the connection is up.

`ping.go:31-78`

```go
if c.options.KeepAlive > 10 {
	checkInterval = 5 * time.Second
} else {
	checkInterval = time.Duration(c.options.KeepAlive) * time.Second / 4
}
...
if time.Since(lastSent) >= time.Duration(c.options.KeepAlive*int64(time.Second)) {
	ping := packets.NewControlPacket(packets.Pingreq).(*packets.PingreqPacket)
	ping.Write(conn)
	c.lastSent.Store(time.Now())
}
```

For `KeepAlive > 10`, checks occur every five seconds. A KeepAlive value of 11 can therefore be observed at the 15-second tick.

`net.go:313-372`

```go
if err := msg.Write(conn); err != nil {
	...
}
...
c.UpdateLastSent()
```

Normal outgoing packets update the same last-sent timestamp used by keepalive.

## Implementation Behavior

The logic sends PINGREQ eventually, but it schedules by polling rather than by the actual deadline. That can create an idle interval longer than MQTT permits.

## Runtime Evidence

What was done:
- Ran a keepalive control where PINGREQ was expected within the configured interval.
- Ran a reproducer with KeepAlive 11 seconds.

Observed result: The control observed PINGREQ `0xc000` after about 1000 ms for a 1000 ms keepalive. The reproducer observed PINGREQ `0xc000` after about 15000 ms for an 11000 ms keepalive.

## Inconsistency Reason

MQTT requires the client to start the next control packet before the Keep Alive interval is exceeded. Paho waits for the next ticker event after the interval has already elapsed.

## Impact

Strict brokers may treat an otherwise healthy idle client as violating Keep Alive and close the connection.

## Fix Direction

Schedule PINGREQ for the actual Keep Alive deadline, or choose a polling interval that guarantees transmission before the deadline for every configured value.
