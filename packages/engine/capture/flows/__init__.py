"""Functional flows — SPEC §8.4 H, §12.3.

Flows produce *data*: a numbered step log, a trace, a video, and a list of what failed.
Group H checkers turn that into Issues, which keeps them pure functions over the artifact
like every other group (CLAUDE.md).
"""

from engine.capture.flows.runner import FlowRunner, run_flows
from engine.capture.flows.steps import Flow, FlowAborted

__all__ = ["Flow", "FlowAborted", "FlowRunner", "run_flows"]
