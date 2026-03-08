import json
from pathlib import Path

from pipeline.parse_logs import infer_module_name, parse_ibex_formal_json, parse_kv_blocks


def test_infer_module_name_hw_ip() -> None:
    assert infer_module_name("hw/ip/aes/dv/cov/aes_cov_if.sv") == "aes"


def test_parse_kv_blocks() -> None:
    text = """addr: 1000
severity: 0
format: okay
addr: 2000
severity: 2
format: CHECK-fail: bad
"""
    blocks = parse_kv_blocks(text)
    assert len(blocks) == 2
    assert blocks[1]["severity"] == "2"


def test_parse_ibex_formal_json() -> None:
    sample = [
        [
            0,
            ["PropA", "PropB"],
            "rIC3",
            [20, 1.0, 2.0, "OK", "RESULT: UNSAT\\n"],
        ],
        [
            0,
            ["PropC"],
            "rIC3",
            [20, 1.0, 2.0, "FAIL", "RESULT: SAT\\n"],
        ],
    ]
    p = Path("data/processed/test_step0_parse_logs.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sample), encoding="utf-8")
    try:
        tests, failures = parse_ibex_formal_json("ibex", "dv/formal/strategies/step0.json", p)
        assert len(tests) == 2
        assert tests[0]["status"] == "pass"
        assert tests[1]["status"] == "fail"
        assert len(failures) == 1
    finally:
        if p.exists():
            p.unlink()
