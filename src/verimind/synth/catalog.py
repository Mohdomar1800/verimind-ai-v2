"""Building blocks for synthetic regressions: design blocks, message templates, and the bug catalog.

The synthetic SoC is a storage controller (NAND controller, ECC, DMA, PCIe endpoint, ...). Every
message has a fixed source location (file + line) and variable data (addresses, data values,
counts), just like real testbench messages. That means the analysis engine has to separate the
"shape" of a message from its data to group failures correctly.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Literal

Category = Literal["design", "testbench", "infrastructure"]
Severity = Literal["UVM_INFO", "UVM_WARNING", "UVM_ERROR", "UVM_FATAL"]
InfraKind = Literal["license", "segfault", "oom"]


# --------------------------------------------------------------------------------------------------
# Design blocks and test scenarios
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Block:
    name: str
    agent: str  # bus protocol of the block's main verification agent
    regs: tuple[str, ...]
    signals: tuple[str, ...]
    irqs: tuple[str, ...]


BLOCKS: tuple[Block, ...] = (
    Block(
        "nand_ctrl",
        "onfi",
        ("NAND_CTRL", "NAND_STATUS", "NAND_TIMING0", "ECC_CFG"),
        ("dq[7:0]", "rb_n", "we_n"),
        ("irq_cmd_done", "irq_ecc_err"),
    ),
    Block(
        "ecc_engine",
        "axi",
        ("ECC_CTRL", "ECC_STATUS", "ECC_THRESH"),
        ("syndrome[15:0]", "dec_valid"),
        ("irq_uncorr",),
    ),
    Block(
        "dma",
        "axi",
        ("DMA_CTRL", "DMA_SRC", "DMA_DST", "DMA_LEN"),
        ("rdata[63:0]", "wvalid"),
        ("irq_dma_done", "irq_dma_err"),
    ),
    Block(
        "pcie_ep",
        "pcie",
        ("PCIE_CTRL", "LINK_STATUS", "BAR0_CFG"),
        ("tlp_data[127:0]", "ltssm_state"),
        ("irq_link_up", "irq_msi"),
    ),
    Block("axi_xbar", "axi", ("XBAR_CFG", "ARB_PRIO"), ("awaddr[31:0]", "bresp[1:0]"), ("irq_decerr",)),
    Block(
        "uart",
        "apb",
        ("UART_CTRL", "UART_BAUD", "UART_FIFO_STATUS"),
        ("rx_data[7:0]", "tx_busy"),
        ("irq_rx_full", "irq_tx_empty"),
    ),
    Block("spi_host", "apb", ("SPI_CTRL", "SPI_CS", "SPI_STATUS"), ("miso", "sck"), ("irq_spi_done",)),
    Block("aes", "apb", ("AES_CTRL", "AES_KEY0", "AES_STATUS"), ("state_out[127:0]", "key_valid"), ("irq_aes_done",)),
    Block("cpu_core", "axi", ("MSTATUS", "MTVEC", "MCAUSE"), ("pc[31:0]", "instr_valid"), ("irq_timer", "irq_ext")),
    Block(
        "power_mgr",
        "apb",
        ("PWR_CTRL", "PWR_STATUS", "WAKE_CFG"),
        ("pwr_state[2:0]", "clk_gate_en"),
        ("irq_wake",),
    ),
)
BLOCKS_BY_NAME = {b.name: b for b in BLOCKS}

SCENARIOS: tuple[str, ...] = ("smoke", "random", "stress", "reset", "error_inject", "perf")

# Relative log length per scenario (a stress test runs far longer than a smoke test).
SCENARIO_LENGTH = {"smoke": 0.4, "random": 1.0, "stress": 2.0, "reset": 0.5, "error_inject": 1.0, "perf": 1.5}


# --------------------------------------------------------------------------------------------------
# Template values
# --------------------------------------------------------------------------------------------------


class TemplateValues(dict[str, object]):
    """Values for `{placeholders}` in message templates.

    Values are generated only when a template asks for them, so each message draws exactly the
    random numbers it needs, and output stays deterministic for a given seed.
    """

    def __init__(self, rng: random.Random, block: Block, **fixed: object) -> None:
        super().__init__(block=block.name, agent=block.agent, AGENT=block.agent.upper(), **fixed)
        self._rng = rng
        self._block = block

    def __missing__(self, key: str) -> object:
        value = self._generate(key)
        self[key] = value
        return value

    def _generate(self, key: str) -> object:
        rng, b = self._rng, self._block
        match key:
            case "addr" | "exp" | "data":
                return f"{rng.getrandbits(32):08x}"
            case "act":  # differs from `exp` by one flipped bit, like a real data corruption
                return f"{int(str(self['exp']), 16) ^ (1 << rng.randrange(32)):08x}"
            case "id":
                return f"{rng.randrange(256):02x}"
            case "n":
                return rng.randint(1, 5000)
            case "len":
                return rng.choice((1, 4, 8, 16, 32, 64, 128, 256))
            case "count":
                return rng.randint(1, 64)
            case "lat":
                return rng.randint(2, 400)
            case "cycles":
                return rng.choice((1000, 5000, 10000, 50000))
            case "page":
                return rng.randint(0, 65535)
            case "offset":
                return f"{rng.randrange(4096):03x}"
            case "syndrome":
                return f"{rng.getrandbits(16):04x}"
            case "state":
                return f"{rng.randrange(8, 16):x}"
            case "reg":
                return rng.choice(b.regs)
            case "signal":
                return rng.choice(b.signals)
            case "irq":
                return rng.choice(b.irqs)
            case "op":
                return rng.choice(("READ", "WRITE"))
            case "seq":
                return f"{b.name}_{rng.choice(('base', 'rand', 'burst', 'cfg', 'stress'))}_seq"
        raise KeyError(key)


def render(template: str, values: TemplateValues) -> str:
    return template.format_map(values)


# --------------------------------------------------------------------------------------------------
# Message and bug templates
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Msg:
    """A UVM report call site: who reports it, from which source line, with which ID and text."""

    component: str
    file: str | None
    line: int
    msg_id: str
    text: str


@dataclass(frozen=True)
class Assertion:
    """A SystemVerilog assertion (SVA) inside the RTL that fails when the design misbehaves."""

    instance: str
    name: str
    file: str
    line: int
    expr: str
    text: str


@dataclass(frozen=True)
class Cascade:
    """A follow-on error caused by the root cause (the noise that buries the real problem)."""

    msg: Msg
    repeat: tuple[int, int] = (1, 1)
    at_end: bool = False  # reported in the report phase (end-of-test checks) instead of during the run


@dataclass(frozen=True)
class BugTemplate:
    key: str
    title: str
    category: Category
    primary: Msg | Assertion
    blocks: tuple[str, ...] | None = None  # None: can live in any block
    scenarios: tuple[str, ...] | None = None  # None: any scenario can trigger it
    primary_severity: Severity = "UVM_ERROR"
    cascade: tuple[Cascade, ...] = ()
    fatal: Msg | None = None  # simulation ends with this UVM_FATAL (e.g. a timeout)
    at_time_zero: bool = False  # fails during build phase, before any traffic
    hit_rate: tuple[float, float] = (0.2, 0.5)  # chance an eligible test hits the bug

    @property
    def signature_hint(self) -> str:
        if isinstance(self.primary, Assertion):
            return f"assertion {self.primary.name}: {self.primary.text}"
        return f"[{self.primary.msg_id}] {self.primary.text}"


@dataclass(frozen=True)
class InfraTemplate:
    """Failures caused by the compute farm, not the design. Triage should route these elsewhere."""

    key: str
    title: str
    kind: InfraKind
    at_start: bool

    @property
    def bug_id(self) -> str:
        return self.key.upper().replace("_", "-")  # e.g. INFRA-LICENSE


# Component paths follow the usual SoC testbench hierarchy: each block has its own environment
# (uvm_test_top.env.<block>_env...). That's what makes "the dma scoreboard" distinct from "the uart
# scoreboard" even in log formats that don't print source file locations.
ENV = "uvm_test_top.env.{block}_env"
SCBD = ENV + ".scbd"
DRV = ENV + ".{agent}_agent.drv"
MON = ENV + ".{agent}_agent.mon"
SCBD_FILE = "tb/{block}_scoreboard.sv"
DRV_FILE = "tb/agents/{agent}/{agent}_driver.sv"
MON_FILE = "tb/agents/{agent}/{agent}_monitor.sv"

MSG_SCBD_MISMATCH = Msg(
    SCBD, SCBD_FILE, 214, "SCBD_MISMATCH", "Data mismatch at addr 0x{addr}: expected 0x{exp}, actual 0x{act}"
)
MSG_SCBD_EOT = Msg(SCBD, SCBD_FILE, 402, "SCBD_EOT", "{count} expected transactions were never observed at end of test")
MSG_RSP_TIMEOUT = Msg(
    DRV, DRV_FILE, 156, "RSP_TIMEOUT", "Timeout waiting for response to txn id=0x{id} after {cycles} cycles"
)
MSG_PH_TIMEOUT = Msg(
    "reporter",
    "/tools/uvm-1.2/src/base/uvm_phase.svh",
    1491,
    "PH_TIMEOUT",
    "Default timeout of {timeout} hit, indicating a probable testbench issue",
)

BUG_CATALOG: tuple[BugTemplate, ...] = (
    BugTemplate(
        key="scbd_mismatch",
        title="Data corruption in {block} datapath (scoreboard mismatch)",
        category="design",
        primary=MSG_SCBD_MISMATCH,
        cascade=(Cascade(MSG_SCBD_MISMATCH, (2, 25)), Cascade(MSG_SCBD_EOT, at_end=True)),
        hit_rate=(0.15, 0.4),
    ),
    BugTemplate(
        key="axi_handshake",
        title="AXI write handshake violation in {block}",
        category="design",
        blocks=("dma", "axi_xbar", "ecc_engine", "cpu_core"),
        primary=Assertion(
            "u_axi_if",
            "a_awvalid_stable",
            "rtl/{block}/{block}_axi_if.sv",
            88,
            "$rose(awvalid) |-> awvalid throughout awready[->1]",
            "AWVALID deasserted before AWREADY handshake completed",
        ),
        cascade=(
            Cascade(
                Msg(
                    MON,
                    MON_FILE,
                    131,
                    "AXI_PROTO",
                    "Protocol violation: write burst id=0x{id} dropped before completion",
                ),
                (1, 3),
            ),
            Cascade(MSG_RSP_TIMEOUT, (1, 4)),
        ),
        fatal=MSG_PH_TIMEOUT,
    ),
    BugTemplate(
        key="fifo_overflow",
        title="RX FIFO overflow in {block}",
        category="design",
        blocks=("uart", "spi_host", "pcie_ep", "nand_ctrl"),
        scenarios=("random", "stress", "perf"),
        primary=Assertion(
            "u_rx_fifo",
            "a_no_overflow",
            "rtl/{block}/{block}_fifo.sv",
            45,
            "!(push && full)",
            "FIFO overflow: push while full (depth={len})",
        ),
        cascade=(
            Cascade(Msg(SCBD, SCBD_FILE, 233, "SCBD_MISSING", "Expected item #{n} never observed on output"), (3, 15)),
            Cascade(MSG_SCBD_EOT, at_end=True),
        ),
        hit_rate=(0.3, 0.6),
    ),
    BugTemplate(
        key="ecc_uncorrectable",
        title="ECC decoder reports uncorrectable errors in {block}",
        category="design",
        blocks=("nand_ctrl", "ecc_engine"),
        scenarios=("random", "stress", "error_inject"),
        primary=Msg(
            ENV + ".ecc_chk",
            "tb/{block}_ecc_checker.sv",
            302,
            "ECC_UNCORR",
            "Uncorrectable ECC error at page {page} offset 0x{offset}: syndrome 0x{syndrome}",
        ),
        cascade=(
            Cascade(
                Msg(
                    SCBD,
                    SCBD_FILE,
                    377,
                    "CRC_MISMATCH",
                    "Read data CRC mismatch for page {page}: expected 0x{exp}, actual 0x{act}",
                ),
                (1, 6),
            ),
        ),
        hit_rate=(0.3, 0.6),
    ),
    BugTemplate(
        key="reset_value",
        title="Wrong register reset value in {block}",
        category="design",
        scenarios=("reset", "smoke"),
        primary=Msg(
            ENV + ".reg_model",
            "tb/{block}_reg_seq.sv",
            96,
            "REG_RESET",
            "Register {reg} reset value mismatch: expected 0x{exp}, actual 0x{act}",
        ),
        hit_rate=(0.5, 0.9),
    ),
    BugTemplate(
        key="fsm_illegal",
        title="Control FSM enters illegal state in {block}",
        category="design",
        blocks=("power_mgr", "pcie_ep", "nand_ctrl", "spi_host"),
        primary=Assertion(
            "u_ctrl_fsm",
            "a_state_legal",
            "rtl/{block}/{block}_ctrl_fsm.sv",
            163,
            "state inside {IDLE, CMD, XFER, DONE}",  # SVA expressions are printed verbatim, never formatted
            "Controller FSM entered illegal state encoding 0x{state}",
        ),
        cascade=(Cascade(MSG_RSP_TIMEOUT, (1, 3)),),
        fatal=MSG_PH_TIMEOUT,
    ),
    BugTemplate(
        key="x_propagation",
        title="X propagation on {block} outputs",
        category="design",
        primary=Msg(MON, MON_FILE, 207, "XCHK", "X/Z value detected on {signal} during active cycle"),
        cascade=(Cascade(MSG_SCBD_MISMATCH, (1, 8)),),
    ),
    BugTemplate(
        key="irq_missing",
        title="Interrupt never fires in {block}",
        category="design",
        blocks=("dma", "uart", "spi_host", "aes", "pcie_ep"),
        primary=Msg(
            ENV + ".irq_mon",
            "tb/common/irq_monitor.sv",
            58,
            "IRQ_TIMEOUT",
            "Expected interrupt {irq} not asserted within {cycles} cycles",
        ),
        fatal=MSG_PH_TIMEOUT,
    ),
    BugTemplate(
        key="cdc_handshake",
        title="Clock-domain crossing without handshake in {block}",
        category="design",
        blocks=("power_mgr", "pcie_ep", "cpu_core"),
        primary=Msg(
            ENV + ".cdc_mon",
            "tb/common/cdc_monitor.sv",
            112,
            "CDC_VIOL",
            "Data on {signal} changed while crossing clk_core->clk_io without req/ack handshake",
        ),
        cascade=(Cascade(MSG_SCBD_MISMATCH, (1, 4)),),
    ),
    BugTemplate(
        key="tb_missing_vif",
        title="Testbench config: virtual interface not set for {agent} driver",
        category="testbench",
        scenarios=("perf",),
        primary=Msg(
            DRV,
            DRV_FILE,
            41,
            "NOVIF",
            "Virtual interface must be set for: uvm_test_top.env.{block}_env.{agent}_agent.drv.vif",
        ),
        primary_severity="UVM_FATAL",
        at_time_zero=True,
        hit_rate=(1.0, 1.0),
    ),
    BugTemplate(
        key="tb_null_object",
        title="Testbench null handle in {block} error-injection sequence",
        category="testbench",
        scenarios=("error_inject", "random"),
        primary=Msg(
            ENV + ".{agent}_agent.sqr",
            "tb/seq/{block}_err_seq.sv",
            77,
            "NULLOBJ",
            "Null object access: response item handle is null in {block}_err_seq.body()",
        ),
        primary_severity="UVM_FATAL",
        hit_rate=(0.3, 0.6),
    ),
    BugTemplate(
        key="tb_ref_model",
        title="Reference model predicts wrong burst length for {block}",
        category="testbench",
        primary=Msg(
            SCBD,
            "tb/{block}_ref_model.sv",
            143,
            "REF_MODEL",
            "Reference model predicted {count} beats for txn id=0x{id} but DUT returned {len}",
        ),
        cascade=(Cascade(MSG_SCBD_EOT, at_end=True),),
    ),
)

INFRA_CATALOG: tuple[InfraTemplate, ...] = (
    InfraTemplate("infra_license", "Simulator license checkout failed", "license", at_start=True),
    InfraTemplate("infra_segfault", "Simulator crashed (segmentation fault)", "segfault", at_start=False),
    InfraTemplate("infra_oom", "Job killed by the farm: out of memory", "oom", at_start=False),
)


# --------------------------------------------------------------------------------------------------
# Everyday messages
# --------------------------------------------------------------------------------------------------

# Normal traffic, with relative weights.
TRAFFIC: tuple[tuple[Msg, float], ...] = (
    (Msg(DRV, DRV_FILE, 88, "{AGENT}_DRV", "Driving {op} txn id=0x{id} addr=0x{addr} len={len}"), 3.0),
    (Msg(MON, MON_FILE, 102, "{AGENT}_MON", "Observed {op} response id=0x{id} resp=OKAY latency={lat} cycles"), 3.0),
    (Msg(SCBD, SCBD_FILE, 180, "SCBD", "Compared transaction #{n}: MATCH"), 2.0),
    (Msg(ENV + ".virt_sqr", "tb/seq/{block}_vseq.sv", 52, "SEQ", "Starting sequence {seq}"), 0.2),
)

# Benign messages. Several contain words like "error" on purpose, because a naive keyword search
# would flag them (the hackathon version did exactly that).
NOISE: tuple[tuple[Severity, Msg], ...] = (
    (
        "UVM_WARNING",
        Msg(
            ENV + ".reg_model",
            "tb/{block}_reg_block.sv",
            31,
            "RegModel",
            "Register {reg} has no backdoor path; using frontdoor access",
        ),
    ),
    (
        "UVM_WARNING",
        Msg(MON, MON_FILE, 119, "SLOW_RSP", "Response latency {lat} cycles exceeds soft limit"),
    ),
    (
        "UVM_INFO",
        Msg(SCBD, SCBD_FILE, 260, "STATS", "Running totals: error_count=0 warning_count={count}"),
    ),
)
NOISE_CFG = Msg(
    "uvm_test_top", "tb/{block}_base_test.sv", 64, "CFG", "Configuration: error_injection_enable=0 max_errors=10"
)
NOISE_ECC_CORRECTED = Msg(
    ENV + ".ecc_chk",
    "tb/{block}_ecc_checker.sv",
    288,
    "ECC_CORR",
    "Corrected {count} single-bit errors (below threshold)",
)
NOISE_NEGATIVE_TEST = Msg(
    SCBD,
    SCBD_FILE,
    311,
    "NEG_TEST",
    "Expected error response received for injected fault (negative test passed)",
)
ECC_BLOCKS = ("nand_ctrl", "ecc_engine")

# Build, reset and report phase messages.
MSG_RUN_TEST = Msg("reporter", None, 0, "RNTST", "Running test {test}...")
MSG_ENV_BUILD = Msg(ENV, "tb/{block}_env.sv", 47, "ENV", "Building {block} environment with {agent} agent")
MSG_RESET_ON = Msg(ENV + ".virt_sqr", "tb/seq/reset_seq.sv", 22, "RST", "Reset asserted")
MSG_RESET_OFF = Msg(ENV + ".virt_sqr", "tb/seq/reset_seq.sv", 29, "RST", "Reset deasserted")
MSG_SCBD_REPORT = Msg(
    SCBD, SCBD_FILE, 420, "SCBD_REPORT", "Scoreboard summary: {matches} matches, {mismatches} mismatches"
)
MSG_TEST_DONE = Msg("uvm_test_top", "tb/{block}_base_test.sv", 131, "TEST_DONE", "** TEST {result} **")
