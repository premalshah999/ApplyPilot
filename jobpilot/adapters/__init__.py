"""Deterministic ATS drivers. Unknown portals use the generic multi-page driver."""

from .base import Adapter, Step
from .icims import ICIMS
from .oracle import Oracle
from .others import Eightfold, Generic, SuccessFactors, Taleo
from .workday import Workday

REGISTRY = {
    "workday": Workday,
    "oracle": Oracle,
    "icims": ICIMS,
    "taleo": Taleo,
    "successfactors": SuccessFactors,
    "eightfold": Eightfold,
}


def adapter_class(ats_id):
    return REGISTRY.get(ats_id, Generic)


def for_ats(ats_id, engine, name=None):
    cls = adapter_class(ats_id)
    if cls is Generic:
        return Generic(engine, ats_id=ats_id, name=name)
    return cls(engine)


__all__ = ["Adapter", "Step", "REGISTRY", "adapter_class", "for_ats"]
