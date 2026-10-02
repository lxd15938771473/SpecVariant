# JDK TLS 1.2 renegotiation is still available for HTTP/2 deployments

## Summary

[RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html) requires HTTP/2 deployments over TLS 1.2 to disable renegotiation, and to treat renegotiation as a connection `PROTOCOL_ERROR`. Netty's OpenSSL engine has code that rejects remote-initiated renegotiation, but the JDK `SslProvider` path still exposes and successfully completes TLS 1.2 renegotiation through `SslHandler.renegotiate()`. There is no HTTP/2-layer enforcement shown here that disables this path or maps it to HTTP/2 `PROTOCOL_ERROR`.

## Standard Requirement

[RFC 9113 Section 9.2](https://www.rfc-editor.org/rfc/rfc9113.html#section-9.2), Use of TLS Features: [RFC 9113 Section 9.2](https://www.rfc-editor.org/rfc/rfc9113.html#section-9.2)

```text
A deployment of HTTP/2 over TLS 1.2 MUST disable renegotiation.  An
endpoint MUST treat a TLS renegotiation as a connection error
(Section 5.4.1) of type PROTOCOL_ERROR.

An endpoint MAY use renegotiation to provide confidentiality
protection for client credentials offered in the handshake, but any
renegotiation MUST occur prior to sending the connection preface.
```

The exception is only for credential protection before the HTTP/2 connection preface. General or post-preface renegotiation is not allowed.

## Relevant Source Code

`handler/src/main/java/io/netty/handler/ssl/SslHandler.java:2164`

```java
/**
 * Performs TLS renegotiation.
 */
public Future<Channel> renegotiate() {
    ChannelHandlerContext ctx = this.ctx;
    if (ctx == null) {
        throw new IllegalStateException();
    }

    return renegotiate(ctx.executor().newPromise());
}

public Future<Channel> renegotiate(final Promise<Channel> promise) {
    ObjectUtil.checkNotNull(promise, "promise");
    ChannelHandlerContext ctx = this.ctx;
    if (ctx == null) {
        throw new IllegalStateException();
    }
    EventExecutor executor = ctx.executor();
    if (!executor.inEventLoop()) {
        executor.execute(() -> renegotiateOnEventLoop(promise));
        return promise;
    }
    renegotiateOnEventLoop(promise);
    return promise;
}
```

`SslHandler` exposes renegotiation as a generic TLS API. This method is not conditioned on HTTP/2 state or the HTTP/2 connection preface.

`handler/src/test/java/io/netty/handler/ssl/RenegotiateTest.java:50`

```java
final SslContext context = SslContextBuilder.forServer(cert.key(), cert.cert())
        .sslProvider(sslProvider)
        .protocols(SslProtocols.TLS_v1_2)
        .build();
```

`handler/src/test/java/io/netty/handler/ssl/RenegotiateTest.java:81`

```java
renegotiate = true;
handler.renegotiate().addListener(future -> {
    if (!future.isSuccess()) {
        error.compareAndSet(null, future.cause());
        ctx.close();
    }
    latch.countDown();
});
```

The shared renegotiation test uses TLS 1.2 and calls `SslHandler.renegotiate()` after the first handshake succeeds.

`handler/src/test/java/io/netty/handler/ssl/JdkSslRenegotiateTest.java:18`

```java
public class JdkSslRenegotiateTest extends RenegotiateTest {

    @Override
    protected SslProvider serverSslProvider() {
        return SslProvider.JDK;
    }
}
```

The JDK provider uses the base `verifyResult`, which fails only if renegotiation reports an error.

`handler/src/test/java/io/netty/handler/ssl/OpenSslRenegotiateTest.java:38`

```java
protected void verifyResult(AtomicReference<Throwable> error) throws Throwable {
    Throwable cause = error.get();
    // Renegotiation is not supported by the OpenSslEngine.
    assertInstanceOf(SSLException.class, cause);
}
```

OpenSSL is different: this test expects renegotiation to fail.

`handler/src/main/java/io/netty/handler/ssl/ReferenceCountedOpenSslEngine.java:1456`

```java
private void rejectRemoteInitiatedRenegotiation() throws SSLHandshakeException {
    if (destroyed || handshakeState != HandshakeState.FINISHED
            || SslProtocols.TLS_v1_3.equals(session.getProtocol())) {
        return;
    }

    int count = SSL.getHandshakeCount(ssl);
    boolean renegotiationAttempted = (!clientMode && count > 1) || (clientMode && count > 2);
    if (renegotiationAttempted) {
        shutdown();
        throw new SSLHandshakeException("remote-initiated renegotiation not allowed");
    }
}
```

This confirms the issue is provider-dependent, not a universal OpenSSL behavior.

## Implementation Behavior

For the JDK provider, Netty can complete a TLS 1.2 renegotiation through `SslHandler.renegotiate()`. The tested path is generic TLS, but Netty's HTTP/2 layer does not show a corresponding guard that disables JDK TLS 1.2 renegotiation for HTTP/2 deployments or converts it to an HTTP/2 connection `PROTOCOL_ERROR`.

## Inconsistency Reason

The standard requires HTTP/2 over TLS 1.2 deployments to disable renegotiation, except for the narrow pre-preface credential-protection case. Netty's JDK provider path leaves renegotiation available and successful after a completed TLS 1.2 handshake. That is inconsistent with an HTTP/2 deployment unless the application adds its own external policy before installing or using HTTP/2.

## Runtime Evidence

The existing `JdkSslRenegotiateTest#testRenegotiateServer` test configured TLS 1.2 with the JDK provider, completed the first handshake, and invoked `SslHandler.renegotiate()`. Renegotiation completed without an error and the test passed. This is a generic TLS test; it does not negotiate HTTP/2 or send an HTTP/2 connection preface, so the run alone does not demonstrate post-preface HTTP/2 behavior.

Command run:

```powershell
cd implementions/netty-4.2
.\mvnw.cmd -pl handler "-Dcheckstyle.skip=true" "-Dtest=JdkSslRenegotiateTest#testRenegotiateServer" test
```

Observed result:

```text
Running io.netty.handler.ssl.JdkSslRenegotiateTest
Tests run: 1, Failures: 0, Errors: 0, Skipped: 0 -- in io.netty.handler.ssl.JdkSslRenegotiateTest
BUILD SUCCESS
```

The passing JDK test means `SslHandler.renegotiate()` completed without an error on the TLS 1.2 JDK provider path.

## Impact

HTTP/2 users that deploy Netty with the JDK TLS provider can accidentally leave TLS 1.2 renegotiation enabled. A peer or application path that renegotiates after the HTTP/2 preface would violate [RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html) and would not be converted by Netty's HTTP/2 layer into the required connection `PROTOCOL_ERROR`.

## Fix Direction

HTTP/2 TLS setup should disable renegotiation for TLS 1.2, or Netty should provide an HTTP/2-aware guard around `SslHandler.renegotiate()` and renegotiation events. Any renegotiation observed after the HTTP/2 connection preface should close the HTTP/2 connection with `PROTOCOL_ERROR`. Provider-specific behavior should be documented because OpenSSL already rejects this path while JDK does not.
