"""
Single entry-point loader for buddy-telemetry sibling modules.

PROBLEM:  buddy-telemetry directory has a hyphen, making normal
          `from .schema import X` invalid Python. Every module was
          independently running importlib.util bootstrap, causing:
          - Duplicate class hierarchies (isinstance fails)
          - Python 3.14 frozen-dataclass crash (sys.modules not set)
          - Inconsistent registration keys

SOLUTION: One loader, one registration, one canonical schema module.
          All siblings import from this file.
"""

from __future__ import annotations

import importlib.util as _ilu
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_MODULE_KEY = "buddy_telemetry_schema"  # single canonical key


def _ensure_schema():
    """Load schema.py exactly once into sys.modules under a stable key."""
    if _MODULE_KEY in _sys.modules:
        return _sys.modules[_MODULE_KEY]
    path = _os.path.join(_HERE, "schema.py")
    spec = _ilu.spec_from_file_location(_MODULE_KEY, path)
    mod = _ilu.module_from_spec(spec)
    _sys.modules[_MODULE_KEY] = mod  # register BEFORE exec (fixes frozen dataclass)
    spec.loader.exec_module(mod)
    return mod


# Eager load on import — ensures schema is available before any sibling
# module's top-level code runs.
schema = _ensure_schema()

# Re-export all public types so siblings can do:
#   from _loader import SessionState, SourceRef, ...
SourceRef = schema.SourceRef
SessionStatus = schema.SessionStatus
HealthGrade = schema.HealthGrade
SessionState = schema.SessionState
AgentState = schema.AgentState
TaskState = schema.TaskState
ResourceSnapshot = schema.ResourceSnapshot
Alert = schema.Alert
RecommendedAction = schema.RecommendedAction
EventType = schema.EventType
MonitorEvent = schema.MonitorEvent

