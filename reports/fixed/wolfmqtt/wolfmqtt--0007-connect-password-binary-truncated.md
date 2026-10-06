# CONNECT Password binary data is truncated by client encoder

MQTT 3.1.1 defines Password as binary data with a two-byte length prefix. wolfMQTT's client CONNECT input exposes only `const char *password`, has no explicit length field, and uses `XSTRLEN` to compute the length. A legal embedded `0x00` byte therefore truncates the password during encoding.

## Standard Requirement

Reference: [MQTT v3.1.1 specification, Section 3.1, CONNECT payload](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html).

The User Name is a UTF-8 encoded string, but Password is `0 to 65535 bytes of binary data` preceded by a two-byte length. Meaning: Password is not a NUL-terminated C string. The byte sequence `41 00 42` is a valid 3-byte Password and should be encoded on the wire with length `0x0003`.

## Relevant Source Code

The public CONNECT input has no password length:

```c
/* Optional login */
const char *username;
const char *password;
```

The encoder first computes Remaining Length using C string length:

```c
if (mc_connect->password) {
    size_t str_len = XSTRLEN(mc_connect->password);
    if (str_len > (size_t)0xFFFF) {
        return MQTT_TRACE_ERROR(MQTT_CODE_ERROR_BAD_ARG);
    }
    remain_len += (int)str_len + MQTT_DATA_LEN_SIZE;
}
```

When writing Password, it calls the binary encoder but still passes `XSTRLEN(password)`:

```c
if (mc_connect->password) {
    tx_payload += MqttEncode_Data(tx_payload,
        (const byte*)mc_connect->password,
        (word16)XSTRLEN(mc_connect->password));
}
```

`MqttEncode_Data` itself copies by explicit length; the bug is that the caller does not have the true binary password length.

The receive side is not the failing point for this report because it reads Password using the on-wire length prefix.

## Runtime Evidence

A focused CONNECT encoder probe was compiled and run. It set:

```c
char password[] = { 'A', '\0', 'B', '\0' };
connect.password = password;
```

Observed output:

```text
MqttEncode_Connect rc=26
encoded: 10 18 00 04 4D 51 54 54 04 C0 00 00 00 03 63 69 64 00 04 75 73 65 72 00 01 41
connect_flags=0xC0
remaining_len=24
password_len_on_wire=1
password_bytes_on_wire: 41
expected_password_len_if_binary_preserved=3
OBSERVED_TRUNCATION_AT_EMBEDDED_NUL
```

Observed result: the on-wire Password field is `00 01 41`. If the binary password had been preserved, it would be `00 03 41 00 42`.

## Decision

The standard allows arbitrary binary Password bytes, but the wolfMQTT client CONNECT encoder cannot preserve bytes after the first embedded NUL.

## Fix Direction

Provide an explicit Password length in the CONNECT API and pass that length to `MqttEncode_Data`.
