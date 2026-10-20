"""Opciones de `process-match-reports` (Fase 14.3): ámbito de grupos. Solo el analizador de argumentos; sin red ni base de datos."""

import pytest

from futsal import cli


def test_group_and_all_groups_are_mutually_exclusive(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["process-match-reports", "--group", "26738244", "--all-groups"])
    assert exc.value.code == 2 and "not allowed with" in capsys.readouterr().err


def test_help_documents_the_scope_options(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["process-match-reports", "--help"])
    out = capsys.readouterr().out
    assert "--group" in out and "--all-groups" in out and "--plan" in out and "grupo inicial" in out
