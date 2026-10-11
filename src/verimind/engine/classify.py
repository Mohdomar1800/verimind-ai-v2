"""Who should look at a failure: design team, testbench owner, or the compute-farm/tools team?

Transparent rules, checked in order. Each returns a category and the reason shown to the user.
Routing matters in practice: a license failure sent to an RTL designer wastes their morning.
"""

from __future__ import annotations

import re
from typing import Literal

from verimind.engine.models import LogEvent, Severity

Category = Literal["design", "testbench", "infrastructure", "unknown"]

_INFRA = re.compile(
    r"licen[cs]e|out of memory|\boom\b|killed|signal \d+|segmentation fault|sigsegv|core dumped|"
    r"internal (?:error|exception)|disk quota|no space left|unable to connect",
    re.IGNORECASE,
)
_TESTBENCH = re.compile(
    r"null (?:object|handle|pointer)|handle is null|virtual interface|\bvif\b|config_db|uvm_config|"
    r"factory|reference model|ref(?:erence)?_model|predictor|not set for|"
    r"sequence item|item handle|cannot find .* in the factory",
    re.IGNORECASE,
)


def classify(event: LogEvent | None) -> tuple[Category, str]:
    if event is None:
        return "unknown", "The log stopped or reported FAILED without an error message to analyze."
    text = " ".join([event.message, event.msg_id or "", *event.details])

    if event.kind == "crash":
        return "infrastructure", "The simulator or the job died, which is usually a tool or compute-farm problem."
    if event.kind == "tool":
        if _INFRA.search(text):
            return "infrastructure", "Tool or environment problem (license, memory, crash), not the design."
        return "infrastructure", "Reported by the simulator itself rather than the testbench or design."
    if event.kind == "assertion":
        return "design", "An assertion inside the design (RTL) fired: the hardware broke one of its own rules."
    if _TESTBENCH.search(text):
        return "testbench", "The message points at testbench code (setup, handles, or the reference model)."
    if event.severity == Severity.FATAL and event.time_ps == 0:  # None means the format has no sim time
        return (
            "testbench",
            "Fatal error before any simulation time passed, which is typical of a testbench setup problem.",
        )
    return "design", "A testbench checker saw the design behave incorrectly."
