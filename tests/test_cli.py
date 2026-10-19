import json

from consensus_robustness.cli import main


def test_cli_run_bft(capsys) -> None:
    rc = main(["run", "--protocol", "bft", "--n", "4", "--byzantine", "1", "--seed", "7", "--gst", "200"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "safety: OK" in out and "trace hash:" in out


def test_cli_run_json_deterministic(capsys) -> None:
    args = [
        "run",
        "--protocol",
        "raft",
        "--n",
        "5",
        "--seed",
        "3",
        "--crash",
        "0@200:600",
        "--partition",
        "0-400:0,1",
        "--gst",
        "400",
        "--json",
    ]
    main(args)
    a = json.loads(capsys.readouterr().out)
    main(args)
    b = json.loads(capsys.readouterr().out)
    assert a["trace_hash"] == b["trace_hash"] and a["safe"] and a["liveness_ok"]


def test_cli_run_mutant_reports_violation(capsys) -> None:
    rc = main(["run", "--protocol", "raft", "--n", "4", "--seed", "169", "--gst", "109", "--mutant", "small_quorum"])
    out = capsys.readouterr().out
    assert rc == 1 and "VIOLATED" in out


def test_cli_sweep(capsys) -> None:
    assert main(["sweep", "--protocol", "bft", "--n", "4", "--seeds", "20"]) == 0
    assert "safety violations: 0" in capsys.readouterr().out
