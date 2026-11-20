import json
from datetime import datetime
from pathlib import Path

import pytest

from mttrmtbf.__main__ import main
from mttrmtbf.core import (Failure, FailureLogError, availability_from, compute, fleet, load_failures,
                           merge_intervals, parse_time)

EX = Path(__file__).resolve().parent.parent / "examples" / "failures.csv"
START, END = datetime(2026, 1, 1), datetime(2026, 11, 1)
WINDOW = 304 * 24.0


def t(text):
    return parse_time(text)


def fail(asset, a, b=None):
    return Failure(asset, t(a), t(b) if b else None)


@pytest.fixture(scope="module")
def metrics():
    return compute(load_failures(str(EX)), START, END)


def test_parse_time_formats():
    assert t("2026-03-02 22:10") == datetime(2026, 3, 2, 22, 10)
    assert t("2026-03-02T22:10") == datetime(2026, 3, 2, 22, 10)
    assert t("2026-03-02") == datetime(2026, 3, 2)


def test_parse_time_rejects_text():
    with pytest.raises(FailureLogError):
        t("last Tuesday")


def test_simple_mttr_and_mtbf():
    m = compute([fail("A", "2026-02-01 00:00", "2026-02-01 10:00"), fail("A", "2026-05-01 00:00", "2026-05-01 02:00")],
                START, END)["A"]
    assert m.failures == 2 and m.downtime_hours == 12.0 and m.mttr_hours == 6.0
    assert m.mtbf_hours == pytest.approx((WINDOW - 12.0) / 2)


def test_availability_matches_uptime_share():
    m = compute([fail("A", "2026-02-01 00:00", "2026-02-02 00:00")], START, END)["A"]
    assert m.availability == pytest.approx(1 - 24.0 / WINDOW)


def test_overlapping_records_are_one_incident(metrics):
    gen = metrics["GEN-1"]
    assert gen.failures == 2 and gen.downtime_hours == 8.0


def test_merge_intervals_touching_and_nested():
    a, b, c = t("2026-01-01"), t("2026-01-02"), t("2026-01-03")
    assert merge_intervals([(b, c), (a, b)]) == [(a, c)]
    assert merge_intervals([(a, c), (a, b)]) == [(a, c)]


def test_open_failure_runs_to_window_end(metrics):
    elev = metrics["ELEV-2"]
    assert elev.open_failures == 1 and elev.failures == 2
    assert elev.downtime_hours == pytest.approx(44.0 + (END - t("2026-09-30 10:30")).total_seconds() / 3600)


def test_failure_outside_window_ignored():
    m = compute([fail("A", "2025-12-31 10:00", "2025-12-31 12:00"), fail("B", "2026-03-01 00:00", "2026-03-01 01:00")],
                START, END)
    assert list(m) == ["B"]


def test_repair_cut_at_window_end():
    m = compute([fail("A", "2026-10-31 12:00", "2026-11-05 12:00")], START, END)["A"]
    assert m.downtime_hours == 12.0 and m.open_failures == 0


def test_repeat_failure_within_a_week(metrics):
    assert metrics["AHU-1"].repeat_failures == 1 and metrics["PUMP-1"].repeat_failures == 0


def test_longest_repair(metrics):
    assert metrics["CHILLER-1"].longest_repair_hours == 54.0


def test_causes_grouped(metrics):
    assert metrics["PUMP-1"].causes["Seal leak"] == 4
    assert metrics["AHU-1"].causes["Fan motor trip"] == 2


def test_fleet_totals(metrics):
    total = fleet(metrics)
    assert total.failures == 19 and total.open_failures == 1
    assert total.mttr_hours == pytest.approx(total.downtime_hours / 19)


def test_window_must_be_positive():
    with pytest.raises(FailureLogError):
        compute([], END, START)


def test_availability_from_formula():
    assert availability_from(900, 100) == 0.9
    with pytest.raises(FailureLogError):
        availability_from(0, 0)


def test_restore_before_failure_rejected(tmp_path):
    f = tmp_path / "f.csv"
    f.write_text("asset_id,failed_at,restored_at,cause\nA,2026-02-02 10:00,2026-02-02 08:00,x\n")
    with pytest.raises(FailureLogError):
        load_failures(str(f))


def test_cli_table_and_exit_code(capsys):
    assert main(["report", str(EX), "--start", "2026-01-01", "--end", "2026-11-01"]) == 1
    assert "PUMP-1" in capsys.readouterr().out
    assert main(["report", str(EX), "--start", "2026-01-01", "--end", "2026-09-01"]) == 0


def test_cli_json_and_sort(capsys):
    main(["report", str(EX), "--start", "2026-01-01", "--end", "2026-11-01", "--format", "json", "--sort", "mtbf"])
    data = json.loads(capsys.readouterr().out)
    assert data[0]["asset"] == "PUMP-1"


def test_cli_errors(tmp_path):
    assert main(["report", str(tmp_path / "x.csv"), "--start", "2026-01-01", "--end", "2026-02-01"]) == 2
    assert main(["report", str(EX), "--start", "2026-02-01", "--end", "2026-01-01"]) == 2
    assert main(["formula", "900", "100"]) == 0
