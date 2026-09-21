import json
from datetime import UTC, datetime
from pathlib import Path

from altruist_tester.artifacts import create_run_artifacts
from altruist_tester.serial_logger import capture_raw_serial

# Fixed artifact timestamps keep JSON/report assertions stable.
STARTED_AT = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)


class FakeSerial:
    def __init__(self, lines, clock):
        self._lines = list(lines)
        self._clock = clock

    def readline(self):
        # Advance on every read so interline gap and silence calculations are
        # deterministic without sleeping in tests.
        self._clock.advance(0.25)
        if self._lines:
            return self._lines.pop(0)
        return b""


class FakeClock:
    """Small monotonic-clock replacement controlled by the fake serial port."""

    def __init__(self):
        self.current = 0.0

    def __call__(self):
        return self.current

    def advance(self, seconds):
        self.current += seconds


def _create_artifacts(tmp_path, *, duration_input="1s", duration_seconds=1):
    return create_run_artifacts(
        tmp_path,
        port=Path("/dev/ttyACM0"),
        baud=115200,
        duration_input=duration_input,
        duration_seconds=duration_seconds,
        started_at=STARTED_AT,
    )


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_capture_raw_serial_writes_raw_log_without_line_events_by_default(tmp_path):
    clock = FakeClock()
    artifacts = _create_artifacts(tmp_path)
    serial = FakeSerial(
        [
            b"first line\r\n",
            b"second line\n",
            b"bad utf8: \xff\n",
        ],
        clock,
    )

    stats = capture_raw_serial(serial, artifacts, 1, clock=clock)

    assert stats.lines_read == 3
    assert stats.bytes_read == 36
    assert stats.first_line_elapsed_seconds == 0.25
    assert stats.last_line_elapsed_seconds == 0.75
    assert stats.max_interline_gap_seconds == 0.25
    assert artifacts.serial_log.read_bytes() == (
        b"first line\r\nsecond line\nbad utf8: \xff\n"
    )

    events = _read_jsonl(artifacts.events_jsonl)
    serial_events = [event for event in events if event["type"] == "serial_line"]
    assert serial_events == []


def test_capture_raw_serial_can_mirror_lines_to_events(tmp_path):
    clock = FakeClock()
    artifacts = _create_artifacts(tmp_path)
    serial = FakeSerial(
        [
            b"first line\r\n",
            b"second line\n",
            b"bad utf8: \xff\n",
        ],
        clock,
    )

    stats = capture_raw_serial(
        serial,
        artifacts,
        1,
        clock=clock,
        mirror_lines_to_events=True,
    )

    assert stats.lines_read == 3
    assert stats.bytes_read == 36

    events = _read_jsonl(artifacts.events_jsonl)
    serial_events = [event for event in events if event["type"] == "serial_line"]
    assert [event["line"] for event in serial_events] == [
        "first line",
        "second line",
        "bad utf8: �",
    ]


def test_capture_raw_serial_writes_keyword_alert_events(tmp_path):
    clock = FakeClock()
    artifacts = _create_artifacts(tmp_path)
    serial = FakeSerial(
        [
            b"normal boot line\n",
            b"Guru Meditation Error: Core 0 panic'ed\n",
            b"[ERROR] [Map] FAILED: WiFi disconnected\n",
        ],
        clock,
    )

    stats = capture_raw_serial(serial, artifacts, 1, clock=clock)

    events = _read_jsonl(artifacts.events_jsonl)
    alert_events = [event for event in events if event["type"] == "keyword_alert"]
    assert [event["code"] for event in alert_events] == [
        "PANIC",
        "GURU_MEDITATION",
    ]
    assert stats.keyword_alerts_count == 2
    assert [alert["code"] for alert in stats.keyword_alerts] == [
        "PANIC",
        "GURU_MEDITATION",
    ]


def test_capture_raw_serial_writes_boot_events(tmp_path):
    clock = FakeClock()
    artifacts = _create_artifacts(tmp_path)
    serial = FakeSerial(
        [
            b"[BOOT] reset_reason=usb_reset_flash_boot reset_code=11 boot=30 "
            b"crash_valid=1 prev_uptime=11 prev_heap=227720 "
            b"last_section_id=0 last_section=Idle/MainLoop heap=192072\n",
        ],
        clock,
    )

    stats = capture_raw_serial(serial, artifacts, 1, clock=clock)

    events = _read_jsonl(artifacts.events_jsonl)
    boot_events = [event for event in events if event["type"] == "boot_event"]
    assert len(boot_events) == 1
    assert boot_events[0]["reset_reason"] == "usb_reset_flash_boot"
    assert boot_events[0]["reset_code"] == 11
    assert boot_events[0]["crash_valid"] is True
    assert boot_events[0]["last_section"] == "Idle/MainLoop"

    assert stats.boot_events.count == 1
    assert stats.boot_events.first_seen == boot_events[0]["ts"]
    assert stats.boot_events.last_seen == boot_events[0]["ts"]
    assert stats.boot_events.last_boot is not None
    assert stats.boot_events.last_boot["prev_uptime_sec"] == 11
    assert stats.boot_event_records[0]["ts"] == boot_events[0]["ts"]


def test_capture_raw_serial_writes_build_events(tmp_path):
    clock = FakeClock()
    artifacts = _create_artifacts(tmp_path)
    serial = FakeSerial(
        [
            b"[BUILD] version=R-URB_2026-07-08-testing+abc1234 "
            b"channel=testing commit=abc1234 model=urban target=esp32c6 "
            b"language=en profile=release\n",
        ],
        clock,
    )

    stats = capture_raw_serial(serial, artifacts, 1, clock=clock)

    events = _read_jsonl(artifacts.events_jsonl)
    build_events = [event for event in events if event["type"] == "build_event"]
    assert len(build_events) == 1
    assert build_events[0]["version"] == "R-URB_2026-07-08-testing+abc1234"
    assert build_events[0]["channel"] == "testing"
    assert build_events[0]["model"] == "urban"
    assert build_events[0]["profile"] == "release"

    assert stats.build_events.count == 1
    assert stats.build_events.first_seen == build_events[0]["ts"]
    assert stats.build_events.last_seen == build_events[0]["ts"]
    assert stats.build_events.last_build is not None
    assert stats.build_events.last_build["target"] == "esp32c6"
    assert stats.build_event_records[0]["ts"] == build_events[0]["ts"]


def test_capture_raw_serial_writes_subsystem_events(tmp_path):
    clock = FakeClock()
    artifacts = _create_artifacts(tmp_path)
    serial = FakeSerial(
        [
            b"[SUBSYSTEM] error subsystem=sd reason=open_append_failed "
            b"path=/data/SDS011/2026-07-24.csv\n",
            b"[SUBSYSTEM] event subsystem=wifi reason=sta_recovery "
            b"mode=deep status=6 ip=0.0.0.0\n",
        ],
        clock,
    )

    stats = capture_raw_serial(serial, artifacts, 1, clock=clock)

    events = _read_jsonl(artifacts.events_jsonl)
    subsystem_events = [event for event in events if event["type"] == "subsystem_event"]
    assert len(subsystem_events) == 2
    assert subsystem_events[0]["level"] == "error"
    assert subsystem_events[0]["subsystem"] == "sd"
    assert subsystem_events[0]["reason"] == "open_append_failed"
    assert subsystem_events[0]["details"] == {"path": "/data/SDS011/2026-07-24.csv"}
    assert subsystem_events[1]["level"] == "event"
    assert subsystem_events[1]["subsystem"] == "wifi"
    assert subsystem_events[1]["details"]["mode"] == "deep"

    assert stats.subsystem_events.count == 2
    assert stats.subsystem_events.first_seen == subsystem_events[0]["ts"]
    assert stats.subsystem_events.last_seen == subsystem_events[1]["ts"]
    assert stats.subsystem_events.by_subsystem == {"sd": 1, "wifi": 1}
    assert stats.subsystem_events.by_reason == {
        "open_append_failed": 1,
        "sta_recovery": 1,
    }
    assert [record["reason"] for record in stats.subsystem_event_records] == [
        "open_append_failed",
        "sta_recovery",
    ]


def test_capture_raw_serial_writes_upload_events(tmp_path):
    clock = FakeClock()
    artifacts = _create_artifacts(tmp_path, duration_input="2s", duration_seconds=2)
    serial = FakeSerial(
        [
            b"[CONNECTIVITY] attempt channel=sensors-connectivity seq=7\n",
            (
                b"[CONNECTIVITY] success channel=sensors-connectivity seq=7 "
                b"host=connectivity.robonomics.network code=200\n"
            ),
            b"[DATALOG] attempt payload_len=55 encoding=cps owner_self_fallback=0\n",
            (
                b"[DATALOG] failed reason=rpc_error code=1010 "
                b"message=Invalid Transaction response_len=111\n"
            ),
            (
                b"[DATALOG] failed reason=payload_too_large encoding=proto "
                b"payload_len=481\n"
            ),
        ],
        clock,
    )

    stats = capture_raw_serial(serial, artifacts, 2, clock=clock)

    events = _read_jsonl(artifacts.events_jsonl)
    upload_events = [event for event in events if event["type"] == "upload_event"]
    assert [event["status"] for event in upload_events] == [
        "attempt",
        "success",
        "attempt",
        "failure",
        "failure",
    ]
    assert upload_events[-1]["raw_fields"] == {
        "reason": "payload_too_large",
        "encoding": "proto",
        "payload_len": "481",
    }
    assert stats.upload_stats.channel("connectivity").attempts == 1
    assert stats.upload_stats.channel("connectivity").successes == 1
    assert stats.upload_stats.channel("datalog").failures == 2
    assert stats.upload_stats.channel("datalog").failure_reasons == {
        "rpc_error code=1010 message=Invalid Transaction response_len=111": 1,
        "payload_too_large encoding=proto payload_len=481": 1,
    }


def test_capture_raw_serial_regresses_current_uart_contract(tmp_path):
    clock = FakeClock()
    artifacts = _create_artifacts(tmp_path, duration_input="4s", duration_seconds=4)
    serial = FakeSerial(
        [
            b"[BUILD] version=R-INS_2026-07-08-testing+abc1234 "
            b"channel=testing commit=abc1234 model=insight target=esp32c6 "
            b"language=en profile=release\n",
            b"[LOG] runtime=3 configured=0 forced=0 core=0\n",
            b"[BOOT] reset_reason=power_on_reset reset_code=1 boot=5 "
            b"crash_valid=0 prev_uptime=0 prev_heap=0 "
            b"last_section_id=0 last_section=Idle/MainLoop heap=219584\n",
            b"[HEALTH] uptime=600 boot=5 heap=219000 rssi=-58 tx=2 "
            b"errors=0 wifi=1 wifi_errors=0 sensor_errors=0 sd_errors=0 "
            b"reset_reason=power_on_reset reset_code=1 crash_valid=0 "
            b"prev_uptime=0 prev_heap=0 last_section_id=0 "
            b"last_section=Idle/MainLoop\n",
            b"[123] [INFO] [PAYLOAD] channel=datalog encoding=cps "
            b"encrypted=1 payload_len=324 sample_available=1 "
            b"sample=h:65.15,t:25.84,co2:723 e1=999\n",
            b"[PAYLOAD] channel=sensors-connectivity encoding=mixed "
            b"encrypted=1 payload_len=360 sample_available=1 "
            b"sample=h:65.15,t:25.84 e1=999\n",
            b"[PAYLOAD] channel=custom-http encoding=cps encrypted=1 "
            b"payload_len=128 sample_available=0\n",
            b"[DATALOG] attempt payload_len=324 encoding=cps owner_self_fallback=0\n",
            b"[DATALOG] failed reason=encryption_failed\n",
            b"[DATALOG] attempt payload_len=324 encoding=cps owner_self_fallback=0\n",
            b"[DATALOG] success response_len=66\n",
            b"[CONNECTIVITY] attempt channel=sensors-connectivity seq=12 "
            b"region=EU host=2.connectivity.robonomics.network\n",
            b"[CONNECTIVITY] failed channel=sensors-connectivity seq=12 "
            b"reason=http_error region=EU host=2.connectivity.robonomics.network "
            b"code=500 response_len=42\n",
            b"[SUBSYSTEM] error subsystem=sensor reason=read_failed name=scd41\n",
        ],
        clock,
    )

    stats = capture_raw_serial(serial, artifacts, 4, clock=clock)

    events = _read_jsonl(artifacts.events_jsonl)
    build_events = [event for event in events if event["type"] == "build_event"]
    boot_events = [event for event in events if event["type"] == "boot_event"]
    health_events = [event for event in events if event["type"] == "dev_metrics"]
    payload_events = [
        event for event in events if event["type"] == "payload_observation"
    ]
    upload_events = [event for event in events if event["type"] == "upload_event"]
    subsystem_events = [event for event in events if event["type"] == "subsystem_event"]

    assert build_events[0]["channel"] == "testing"
    assert build_events[0]["profile"] == "release"
    assert boot_events[0]["reset_reason"] == "power_on_reset"
    assert health_events[0]["uptime_sec"] == 600
    assert health_events[0]["last_section"] == "Idle/MainLoop"

    assert [
        (
            event["channel"],
            event["encoding"],
            event["encrypted"],
            event["payload_len"],
            event["sample_available"],
        )
        for event in payload_events
    ] == [
        ("datalog", "cps", True, 324, True),
        ("sensors-connectivity", "mixed", True, 360, True),
        ("custom-http", "cps", True, 128, False),
    ]
    assert "sample" not in payload_events[0]["raw_fields"]

    samples = _read_jsonl(artifacts.samples_jsonl)
    assert [
        (sample["metric"], sample["value"], sample["source"]) for sample in samples
    ] == [
        ("humidity", 65.15, "serial_payload_datalog"),
        ("temperature", 25.84, "serial_payload_datalog"),
        ("co2", 723.0, "serial_payload_datalog"),
        ("humidity", 65.15, "serial_payload_connectivity"),
        ("temperature", 25.84, "serial_payload_connectivity"),
    ]

    assert [event["status"] for event in upload_events] == [
        "attempt",
        "failure",
        "attempt",
        "success",
        "attempt",
        "failure",
    ]
    assert upload_events[0]["reason"] == (
        "payload_len=324 encoding=cps owner_self_fallback=0"
    )
    assert upload_events[1]["reason"] == "encryption_failed"
    assert upload_events[4]["target"] == "2.connectivity.robonomics.network"
    assert upload_events[4]["reason"] == "region=EU"
    assert upload_events[5]["reason"] == "http_error region=EU code=500 response_len=42"
    assert subsystem_events[0]["reason"] == "read_failed"
    assert subsystem_events[0]["details"] == {"name": "scd41"}

    assert stats.build_events.count == 1
    assert stats.boot_events.count == 1
    assert stats.dev_metrics.count == 1
    assert stats.payload_observations.count == 3
    assert stats.sensor_samples_count == 5
    assert stats.upload_stats.channel("datalog").attempts == 2
    assert stats.upload_stats.channel("datalog").failure_reasons == {
        "encryption_failed": 1
    }
    assert stats.upload_stats.channel("connectivity").failure_reasons == {
        "http_error region=EU code=500 response_len=42": 1
    }


def test_capture_raw_serial_ignores_non_contract_upload_lines(tmp_path):
    clock = FakeClock()
    artifacts = _create_artifacts(tmp_path, duration_input="2s", duration_seconds=2)
    serial = FakeSerial(
        [
            b"API Name: Robonomics Datalog\n",
            b"  Count Sends: 0\n",
            b"  Last Send Time: Thu Jan  1 00:00:00 1970\n",
            b"  Is OK: Yes\n",
            b"API Name: Robonomics Datalog\n",
            b"  Count Sends: 1\n",
            b"  Last Send Time: Mon Jun 22 10:10:30 2026\n",
            b"  Is OK: Yes\n",
            b"API Name: Robonomics Datalog\n",
            b"  Count Sends: 1\n",
            b"  Last Send Time: Mon Jun 22 10:10:30 2026\n",
            b"  Is OK: Yes\n",
            b"Extrinsic Datalog: size 199\n",
            b'Extrinsic result: "0x848cc48cd5d47200d08f3212976018e3e98eaf"'
            b"[603364] [INFO] [Urban LED] mode: : GREEN\n",
            b"[DEBUG] [Datalog] Sending: : h:59.46,t:25.32\n",
            b'Extrinsic result: "0xe1f85b"[DEBUG] [Datalog] OK, result: : "0xe1f85b"\n',
        ],
        clock,
    )

    stats = capture_raw_serial(serial, artifacts, 2, clock=clock)

    events = _read_jsonl(artifacts.events_jsonl)
    upload_events = [event for event in events if event["type"] == "upload_event"]
    assert upload_events == []
    assert stats.upload_stats.channel("datalog").successes == 0
    assert stats.upload_stats.channel("datalog").attempts == 0


def test_capture_raw_serial_records_device_identity(tmp_path):
    clock = FakeClock()
    artifacts = _create_artifacts(tmp_path)
    serial = FakeSerial(
        [
            b"[123] [INFO] ChipId: : 1051DB010C70\n",
            b"[124] [INFO] ChipId: : 1051DB010C70\n",
        ],
        clock,
    )

    stats = capture_raw_serial(serial, artifacts, 1, clock=clock)

    events = _read_jsonl(artifacts.events_jsonl)
    identity_events = [
        event for event in events if event["type"] == "device_identity_observed"
    ]
    assert identity_events == [
        {
            "ts": identity_events[0]["ts"],
            "type": "device_identity_observed",
            "source": "serial_log",
            "device_id": "1051DB010C70",
        }
    ]
    assert stats.serial_device_ids == ("1051DB010C70",)


def test_capture_raw_serial_writes_sensor_samples(tmp_path):
    clock = FakeClock()
    artifacts = _create_artifacts(tmp_path)
    serial = FakeSerial(
        [
            (
                b'{"service_data":{"signal_strength":-38},'
                b'"BME280":{"temperature":{"value":25.5,"units":"C"}},'
                b'"SDS":{"P1":{"value":16.3,"units":"ppm"}}}\n'
            ),
            (
                b"[123] [INFO] [PAYLOAD] channel=sensors-connectivity encoding=plain "
                b"encrypted=0 payload_len=24 sample_available=1 "
                b"sample=h:65.99,t:25.51,p1:16.33\n"
            ),
        ],
        clock,
    )

    stats = capture_raw_serial(serial, artifacts, 1, clock=clock)

    samples = _read_jsonl(artifacts.samples_jsonl)
    assert [
        (sample["sensor"], sample["metric"], sample["value"]) for sample in samples
    ] == [
        ("BME280", "temperature", 25.5),
        ("SDS", "P1", 16.3),
        ("sensors-connectivity", "humidity", 65.99),
        ("sensors-connectivity", "temperature", 25.51),
        ("sensors-connectivity", "P1", 16.33),
    ]
    assert stats.sensor_samples_count == 5
    assert stats.sensor_series.latest(("BME280", "temperature")).value == 25.5
    assert (
        stats.sensor_series.latest(("sensors-connectivity", "humidity")).value == 65.99
    )


def test_capture_raw_serial_does_not_duplicate_payload_samples_by_upload_channel(
    tmp_path,
):
    clock = FakeClock()
    artifacts = _create_artifacts(tmp_path)
    serial = FakeSerial(
        [
            (
                b"[PAYLOAD] channel=sensors-connectivity encoding=plain "
                b"encrypted=0 payload_len=15 sample_available=1 "
                b"sample=h:65.99,t:25.51\n"
            ),
            (
                b"[PAYLOAD] channel=datalog encoding=plain encrypted=0 "
                b"payload_len=15 sample_available=1 "
                b"sample=h:65.99,t:25.51\n"
            ),
        ],
        clock,
    )

    stats = capture_raw_serial(serial, artifacts, 1, clock=clock)

    samples = _read_jsonl(artifacts.samples_jsonl)
    assert [
        (sample["metric"], sample["value"], sample["source"]) for sample in samples
    ] == [
        ("humidity", 65.99, "serial_payload_connectivity"),
        ("temperature", 25.51, "serial_payload_connectivity"),
    ]
    assert stats.sensor_samples_count == 2
    assert stats.payload_observations.count == 2
    assert stats.payload_observations.by_channel == {
        "datalog": 1,
        "sensors-connectivity": 1,
    }
    assert stats.payload_observations.by_encoding == {"plain": 2}
    assert stats.payload_observations.sample_available_count == 2
    assert stats.payload_observation_records == (
        {
            "ts": stats.payload_observation_records[0]["ts"],
            "channel": "sensors-connectivity",
            "encoding": "plain",
            "encrypted": False,
            "payload_len": 15,
            "sample_available": True,
            "raw_fields": {
                "channel": "sensors-connectivity",
                "encoding": "plain",
                "encrypted": "0",
                "payload_len": "15",
                "sample_available": "1",
            },
        },
        {
            "ts": stats.payload_observation_records[1]["ts"],
            "channel": "datalog",
            "encoding": "plain",
            "encrypted": False,
            "payload_len": 15,
            "sample_available": True,
            "raw_fields": {
                "channel": "datalog",
                "encoding": "plain",
                "encrypted": "0",
                "payload_len": "15",
                "sample_available": "1",
            },
        },
    )

    payload_events = [
        event
        for event in _read_jsonl(artifacts.events_jsonl)
        if event["type"] == "payload_observation"
    ]
    assert len(payload_events) == 2
    assert payload_events[0]["channel"] == "sensors-connectivity"
    assert payload_events[1]["channel"] == "datalog"


def test_capture_raw_serial_reports_progress(tmp_path):
    clock = FakeClock()
    artifacts = _create_artifacts(
        tmp_path,
        duration_input="2s",
        duration_seconds=2,
    )
    serial = FakeSerial(
        [
            b'{"BME280":{"temperature":{"value":25.5,"units":"C"}}}\n',
            b"Guru Meditation Error: Core 0 panic'ed\n",
            b"[HEALTH] uptime=3600 boot=4 heap=219584 rssi=-62 tx=12 "
            b"errors=0 wifi=1 wifi_errors=0 sensor_errors=0 sd_errors=0 "
            b"reset_reason=power_on_reset reset_code=1 crash_valid=0 "
            b"prev_uptime=0 prev_heap=0 last_section_id=0 "
            b"last_section=Idle/MainLoop\n",
        ],
        clock,
    )
    progress_updates = []

    capture_raw_serial(
        serial,
        artifacts,
        2,
        clock=clock,
        progress_callback=progress_updates.append,
        progress_interval_seconds=0.5,
    )

    assert progress_updates
    # The logger always emits a final complete update even when the interval
    # callback cadence does not land exactly on the run duration.
    assert progress_updates[-1].complete is True
    assert progress_updates[-1].lines_read == 3
    assert progress_updates[-1].dev_metrics_count == 1
    assert progress_updates[-1].keyword_alerts_count == 2
    assert progress_updates[-1].sensor_samples_count == 1
    assert progress_updates[-1].percent == 100.0


def test_capture_raw_serial_writes_dev_metrics_events(tmp_path):
    clock = FakeClock()
    artifacts = _create_artifacts(
        tmp_path,
        duration_input="5s",
        duration_seconds=5,
    )
    serial = FakeSerial(
        [
            b"[HEALTH] uptime=121 boot=7 heap=220000 rssi=-81 tx=1 "
            b"errors=3 wifi=1 wifi_errors=0 sensor_errors=1 sd_errors=2 "
            b"reset_reason=power_on_reset reset_code=1 crash_valid=0 "
            b"prev_uptime=0 prev_heap=0 last_section_id=0 "
            b"last_section=Idle/MainLoop\n",
            b"[HEALTH] uptime=124 boot=7 heap=219584 rssi=-79 tx=2 "
            b"errors=4 wifi=1 wifi_errors=3 sensor_errors=0 sd_errors=1 "
            b"reset_reason=power_on_reset reset_code=1 crash_valid=0 "
            b"prev_uptime=0 prev_heap=0 last_section_id=0 "
            b"last_section=Idle/MainLoop\n",
        ],
        clock,
    )

    stats = capture_raw_serial(serial, artifacts, 5, clock=clock)

    events = _read_jsonl(artifacts.events_jsonl)
    metrics_events = [event for event in events if event["type"] == "dev_metrics"]
    assert len(metrics_events) == 2
    assert metrics_events[0]["uptime_sec"] == 121
    assert metrics_events[0]["errors"] == {"wifi": 0, "sensor": 1, "sd": 2}
    assert metrics_events[1]["uptime_sec"] == 124
    assert metrics_events[1]["rssi"] == -79

    # Check both the persisted health event shape and the aggregate summary used
    # later by runtime-counter rules.
    assert stats.dev_metrics.count == 2
    assert [record["uptime_sec"] for record in stats.dev_metrics_records] == [121, 124]
    assert [record["boot"] for record in stats.dev_metrics_records] == [7, 7]
    assert stats.dev_metrics_records[0]["ts"] == metrics_events[0]["ts"]
    assert stats.dev_metrics.first_seen == metrics_events[0]["ts"]
    assert stats.dev_metrics.last_seen == metrics_events[1]["ts"]
    assert stats.dev_metrics.max_boot == 7
    assert stats.dev_metrics.min_uptime_sec == 121
    assert stats.dev_metrics.max_uptime_sec == 124
    assert stats.dev_metrics.min_rssi == -81
    assert stats.dev_metrics.max_rssi == -79
    assert stats.dev_metrics.min_esp_temp_c is None
    assert stats.dev_metrics.max_esp_temp_c is None
    assert stats.dev_metrics.max_errors == {"wifi": 3, "sensor": 1, "sd": 2}
    assert stats.dev_metrics.last_metrics == {
        "model": None,
        "status": "ERROR",
        "uptime_sec": 124,
        "boot": 7,
        "wifi_state": "OK",
        "rssi": -79,
        "tx": 2,
        "last_tx_age_sec": None,
        "errors": {"wifi": 3, "sensor": 0, "sd": 1},
        "esp_temp_c": None,
        "free_heap": 219584,
        "error_count": 4,
        "reset_reason": "power_on_reset",
        "reset_code": 1,
        "crash_valid": False,
        "prev_uptime_sec": 0,
        "prev_free_heap": 0,
        "last_section_id": 0,
        "last_section": "Idle/MainLoop",
    }


def test_capture_raw_serial_ignores_short_health_event(tmp_path):
    clock = FakeClock()
    artifacts = _create_artifacts(
        tmp_path,
        duration_input="2s",
        duration_seconds=2,
    )
    serial = FakeSerial(
        [b"[HEALTH] uptime=3600 boot=4 heap=219584 rssi=-62 tx=12 errors=0\n"],
        clock,
    )

    stats = capture_raw_serial(serial, artifacts, 2, clock=clock)

    events = _read_jsonl(artifacts.events_jsonl)
    metrics_events = [event for event in events if event["type"] == "dev_metrics"]
    assert metrics_events == []
    assert stats.dev_metrics.count == 0


def test_capture_raw_serial_writes_health_event_with_reset_context(tmp_path):
    clock = FakeClock()
    artifacts = _create_artifacts(
        tmp_path,
        duration_input="2s",
        duration_seconds=2,
    )
    serial = FakeSerial(
        [
            b"[HEALTH] uptime=3600 boot=4 heap=219584 rssi=-62 tx=12 "
            b"errors=3 wifi=1 wifi_errors=1 sensor_errors=2 sd_errors=0 "
            b"reset_reason=power_on_reset reset_code=1 crash_valid=0 "
            b"prev_uptime=0 prev_heap=0 last_section_id=0 "
            b"last_section=Idle/MainLoop\n"
        ],
        clock,
    )

    stats = capture_raw_serial(serial, artifacts, 2, clock=clock)

    events = _read_jsonl(artifacts.events_jsonl)
    metrics_events = [event for event in events if event["type"] == "dev_metrics"]
    assert len(metrics_events) == 1
    assert metrics_events[0]["errors"] == {"wifi": 1, "sensor": 2, "sd": 0}
    assert metrics_events[0]["error_count"] == 3
    assert metrics_events[0]["reset_reason"] == "power_on_reset"
    assert metrics_events[0]["last_section"] == "Idle/MainLoop"
    assert stats.dev_metrics.max_errors == {"wifi": 1, "sensor": 2, "sd": 0}
