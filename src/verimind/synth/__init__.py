"""Synthetic RTL verification regressions with a known answer key.

Real regression logs are proprietary, so VeriMind ships a generator that writes realistic logs
(VCS, Xcelium, Questa, and an in-house runner style) with injected bugs. It also writes down
exactly where each failure starts, so the analysis can be measured against the truth.
"""

from verimind.synth.answer_key import AnswerKey
from verimind.synth.generator import PRESETS, DatasetSpec, GenerationReport, generate_dataset

__all__ = ["PRESETS", "AnswerKey", "DatasetSpec", "GenerationReport", "generate_dataset"]
