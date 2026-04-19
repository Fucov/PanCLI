from __future__ import annotations

from pansh.progress import Speedometer, format_eta, format_rate


def test_speedometer_smooths_speed() -> None:
    meter = Speedometer(alpha=0.5, started_at=0.0, last_at=0.0)
    meter.update(100, now=1.0)
    first = meter.current_speed
    meter.update(300, now=2.0)
    assert meter.current_speed > 0
    assert meter.current_speed != 200.0
    assert meter.average_speed == 150.0
    assert first > 0


def test_format_helpers_handle_empty_values() -> None:
    assert format_rate(0) == "-"
    assert format_eta(None) == "-"
