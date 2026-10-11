from __future__ import annotations

from verimind.engine.models import LogEvent, Severity
from verimind.engine.signature import SignatureIndex, mask


def event(
    message: str,
    *,
    file: str | None = None,
    line: int | None = None,
    kind: str = "message",
    msg_id: str | None = None,
    source: str | None = None,
) -> LogEvent:
    return LogEvent(
        line=1, severity=Severity.ERROR, kind=kind, rule="t", message=message,  # type: ignore[arg-type]
        file=file, file_line=line, msg_id=msg_id, source=source,
    )  # fmt: skip


def test_mask_removes_variable_data_but_keeps_identifiers() -> None:
    assert mask("addr 0x1149054d: expected 0xFF, got 12 after 8899 NS on AES_KEY0") == (
        "addr <hex>: expected <hex>, got <n> after <time> on AES_KEY0"
    )
    assert mask("syndrome deadbeef01 at dq[7:0]") == "syndrome <hex> at dq[<n>:<n>]"


def test_same_call_site_is_one_signature_whatever_the_data() -> None:
    index = SignatureIndex()
    a = index.assign(event("Register CTRL reset value mismatch: expected 0x0", file="tb/r.sv", line=96, msg_id="R"))
    b = index.assign(event("Register STATUS reset value mismatch: expected 0x8", file="tb/r.sv", line=96, msg_id="R"))
    assert a is b
    assert a.text == "Register <*> reset value mismatch: expected <hex>"


def test_different_call_sites_are_different_signatures() -> None:
    index = SignatureIndex()
    a = index.assign(event("Data mismatch", file="tb/dma_scoreboard.sv", line=214, msg_id="SCBD"))
    b = index.assign(event("Data mismatch", file="tb/uart_scoreboard.sv", line=214, msg_id="SCBD"))
    assert a.signature_id != b.signature_id


def test_location_less_messages_merge_only_when_similar() -> None:
    index = SignatureIndex()
    a = index.assign(event("License checkout failed for feature VCSRuntime_Net on lic-srv01", kind="tool"))
    b = index.assign(event("License checkout failed for feature VCSRuntime_Net on lic-srv02", kind="tool"))
    c = index.assign(event("Elaboration failed: module ddr_phy not found", kind="tool"))
    assert a is b
    assert c is not a


def test_signature_ids_are_stable_across_runs() -> None:
    e = event("Data mismatch at 0x1", file="tb/s.sv", line=5, msg_id="SCBD")
    assert SignatureIndex().assign(e).signature_id == SignatureIndex().assign(e).signature_id


def test_assertion_title_names_the_assertion() -> None:
    sig = SignatureIndex().assign(
        event("FIFO overflow", kind="assertion", file="rtl/f.sv", line=45, source="tb_top.dut.u_uart.a_no_overflow")
    )
    assert sig.title == "Assertion a_no_overflow failed: FIFO overflow"


def test_identity_does_not_depend_on_format_specific_location() -> None:
    """VCS prints file:line, the in-house runner doesn't; the same problem must keep one id."""
    with_location = event("Data mismatch at 0x1", file="tb/s.sv", line=214, msg_id="SCBD", source="env.scbd")
    without = event("Data mismatch at 0x2", msg_id="SCBD", source="env.scbd")
    assert SignatureIndex().assign(with_location).signature_id == SignatureIndex().assign(without).signature_id
