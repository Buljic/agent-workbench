from workbench.cli import demo, main


def test_demo_completes_and_prints_audit(capsys):
    assert demo() == 0

    out = capsys.readouterr().out
    assert "status: COMPLETED" in out
    assert "audit:" in out
    assert any("run_finished" in line for line in out.splitlines())


def test_unknown_command_returns_2():
    assert main(["nope"]) == 2
