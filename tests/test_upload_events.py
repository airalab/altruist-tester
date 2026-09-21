from altruist_tester.parsers.upload_events import parse_upload_event


def test_parse_connectivity_upload_lines():
    attempt = parse_upload_event(
        "[CONNECTIVITY] attempt channel=sensors-connectivity seq=42 "
        "region=EU host=2.connectivity.robonomics.network"
    )
    success = parse_upload_event(
        "[CONNECTIVITY] success channel=sensors-connectivity seq=42 "
        "region=EU host=2.connectivity.robonomics.network code=200"
    )
    failure = parse_upload_event(
        "[CONNECTIVITY] failed channel=sensors-connectivity seq=43 "
        "reason=http_error host=connectivity.robonomics.network code=500 "
        "response_len=128"
    )

    assert attempt is not None
    assert attempt.as_event_payload() == {
        "channel": "connectivity",
        "status": "attempt",
        "sequence": 42,
        "target": "2.connectivity.robonomics.network",
        "reason": "region=EU",
        "raw_fields": {
            "region": "EU",
            "host": "2.connectivity.robonomics.network",
        },
    }
    assert success is not None
    assert success.as_event_payload() == {
        "channel": "connectivity",
        "status": "success",
        "sequence": 42,
        "target": "2.connectivity.robonomics.network",
        "reason": "region=EU code=200",
        "raw_fields": {
            "region": "EU",
            "host": "2.connectivity.robonomics.network",
            "code": "200",
        },
    }
    assert failure is not None
    assert failure.as_event_payload() == {
        "channel": "connectivity",
        "status": "failure",
        "sequence": 43,
        "target": "connectivity.robonomics.network",
        "reason": "http_error code=500 response_len=128",
        "raw_fields": {
            "reason": "http_error",
            "host": "connectivity.robonomics.network",
            "code": "500",
            "response_len": "128",
        },
    }


def test_parse_connectivity_upload_lines_with_optional_fields():
    attempt = parse_upload_event(
        "[CONNECTIVITY] attempt channel=sensors-connectivity seq=44 "
        "payload_len=128 encoding=mixed"
    )
    failure = parse_upload_event(
        "[CONNECTIVITY] failed channel=sensors-connectivity seq=45 "
        "reason=no_server_available region=EU host=-"
    )
    local_failure = parse_upload_event(
        "[CONNECTIVITY] failed channel=sensors-connectivity seq=46 "
        "reason=wifi_disconnected"
    )

    assert attempt is not None
    assert attempt.reason == "payload_len=128 encoding=mixed"
    assert failure is not None
    assert failure.as_event_payload() == {
        "channel": "connectivity",
        "status": "failure",
        "sequence": 45,
        "target": "-",
        "reason": "no_server_available region=EU",
        "raw_fields": {
            "reason": "no_server_available",
            "region": "EU",
            "host": "-",
        },
    }
    assert local_failure is not None
    assert local_failure.as_event_payload() == {
        "channel": "connectivity",
        "status": "failure",
        "sequence": 46,
        "target": None,
        "reason": "wifi_disconnected",
        "raw_fields": {"reason": "wifi_disconnected"},
    }


def test_parse_datalog_upload_lines():
    attempt = parse_upload_event(
        "[DATALOG] attempt payload_len=55 encoding=cps owner_self_fallback=0"
    )
    success = parse_upload_event("[DATALOG] success response_len=68")
    failure = parse_upload_event(
        "[DATALOG] failed reason=rpc_error code=1010 "
        "message=Invalid Transaction response_len=111"
    )

    assert attempt is not None
    assert attempt.as_event_payload() == {
        "channel": "datalog",
        "status": "attempt",
        "sequence": None,
        "target": None,
        "reason": "payload_len=55 encoding=cps owner_self_fallback=0",
        "raw_fields": {
            "payload_len": "55",
            "encoding": "cps",
            "owner_self_fallback": "0",
        },
    }
    assert success is not None
    assert success.as_event_payload() == {
        "channel": "datalog",
        "status": "success",
        "sequence": None,
        "target": None,
        "reason": "response_len=68",
        "raw_fields": {"response_len": "68"},
    }
    assert failure is not None
    assert failure.as_event_payload() == {
        "channel": "datalog",
        "status": "failure",
        "sequence": None,
        "target": None,
        "reason": "rpc_error code=1010 message=Invalid Transaction response_len=111",
        "raw_fields": {
            "reason": "rpc_error",
            "code": "1010",
            "message": "Invalid Transaction",
            "response_len": "111",
        },
    }


def test_parse_datalog_attempt_allows_optional_fields():
    minimal = parse_upload_event("[DATALOG] attempt payload_len=55")
    extended = parse_upload_event(
        "[DATALOG] attempt payload_len=324 encoding=cps owner_self_fallback=1 "
        "batch_items=6"
    )

    assert minimal is not None
    assert minimal.reason == "payload_len=55"
    assert extended is not None
    assert extended.reason == (
        "payload_len=324 encoding=cps owner_self_fallback=1 batch_items=6"
    )


def test_parse_datalog_local_failure_reasons():
    for reason in ("payload_empty", "encryption_failed", "payload_too_large"):
        event = parse_upload_event(f"[DATALOG] failed reason={reason}")

        assert event is not None
        assert event.as_event_payload() == {
            "channel": "datalog",
            "status": "failure",
            "sequence": None,
            "target": None,
            "reason": reason,
            "raw_fields": {"reason": reason},
        }


def test_parse_datalog_failure_retains_extended_proto_fields():
    event = parse_upload_event(
        "[DATALOG] failed reason=payload_too_large encoding=proto payload_len=481"
    )

    assert event is not None
    assert event.as_event_payload() == {
        "channel": "datalog",
        "status": "failure",
        "sequence": None,
        "target": None,
        "reason": "payload_too_large encoding=proto payload_len=481",
        "raw_fields": {
            "reason": "payload_too_large",
            "encoding": "proto",
            "payload_len": "481",
        },
    }


def test_parse_datalog_statuses_accept_extended_or_missing_optional_fields():
    attempt = parse_upload_event("[DATALOG] attempt encoding=proto payload_len=481")
    success = parse_upload_event("[DATALOG] success target=chain-a receipt=0xabc")
    bare_success = parse_upload_event("[DATALOG] success")
    missing_reason = parse_upload_event("[DATALOG] failed encoding=proto")

    assert attempt is not None
    assert attempt.raw_fields == {"encoding": "proto", "payload_len": "481"}
    assert success is not None
    assert success.raw_fields == {"target": "chain-a", "receipt": "0xabc"}
    assert bare_success is not None
    assert bare_success.raw_fields == {}
    assert missing_reason is None


def test_parse_upload_event_ignores_non_contract_lines():
    non_contract_lines = [
        "Status: ALIVE",
        "[123] [INFO] [Map#42] Send attempt",
        "[123] [INFO] [Map#42] POST to 1.connectivity.robonomics.network:65/",
        "[123] [INFO] [Map#42] OK, POST succeeded -> 1.connectivity.robonomics.network",
        "[ERROR] [Map] FAILED: server returned HTTP error",
        "[Datalog] Sending: h:45,t:24",
        "[Datalog] OK, result: 0x123",
        "[Datalog] FAILED",
        "[Datalog] WARNING: data string is empty",
        "Extrinsic Datalog: size 199",
        'Extrinsic result: "0x848cc48cd5d47200d08f3212976018e3e98eaf"',
        "API Name: Robonomics Datalog",
        "  Count Sends: 2",
        "  Is OK: Yes",
    ]

    assert [parse_upload_event(line) for line in non_contract_lines] == [None] * len(
        non_contract_lines
    )
