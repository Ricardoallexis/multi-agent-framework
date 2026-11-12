# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""MOD-T04: the one privacy decision taken before a model receives a task (contract MOD section 3).

A pure function, shared by the executor service (``_execute``, check 1, which covers ``run_task`` and ``resume_task``)
and by each adapter (check 2, before it builds a prompt). It reads only what the caller hands it: the data class
resolved from a trusted source (never from the text of a task), and what the catalog says about the model. It never
reads a task, a path or a prompt, and what it returns names a reason code, never content.

Rules, in this order:

* ``provider_rejected``: the executor does not take the model's provider (the caller computed that from the catalog and the
  adapter, see ``executors.selection.model_exclusions``). No silent replacement is looked for.
* A class the vocabulary does not know is ``privacy_class_unknown``; so is a missing class when the catalog restricts the
  model (``allowed_data_classes``) or the profile does. A model the catalog does not describe and no profile restricts has
  no declared limit, so a MISSING class does not stop it (a legacy run, MOD-D2=C); it gets warnings instead.
* A KNOWN class needs a list that includes it: ``allowed_data_classes`` of the model or of the profile. Nothing declared
  is not "no limit" (contract section 3: the classes must be *included*): ``privacy_class_unknown``.
* ``data_class_not_allowed``: the class is not in the model's ``allowed_data_classes`` or the profile's. Both must hold.
* ``restricted`` never leaves the local host: it needs ``host == local`` and a known retention. An unknown host or retention
  blocks ``restricted`` and nothing else, and is a warning for the evaluation of MOD-T05 (no event is written here).

A denial is a level 0 ``Stop`` of the autonomy policy (AXI-T01): never an approval to wait for.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

NON_SENSITIVE = "non_sensitive"
SENSITIVE = "sensitive"
RESTRICTED = "restricted"
DATA_CLASSES = (NON_SENSITIVE, SENSITIVE, RESTRICTED)

HOSTS = ("local", "organization", "provider_hosted", "third_party", "unknown")
RETENTION_STATUSES = ("none", "zero_retention", "limited", "provider_default", "unknown")

PRIVACY_CLASS_UNKNOWN = "privacy_class_unknown"
DATA_CLASS_NOT_ALLOWED = "data_class_not_allowed"
PROVIDER_REJECTED = "provider_rejected"       # the only reason MOD-T04 adds (contract section 3)
REASONS = (PRIVACY_CLASS_UNKNOWN, DATA_CLASS_NOT_ALLOWED, PROVIDER_REJECTED)

STOP_LEVEL = 0


def _classes(value: Any) -> frozenset[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return frozenset({value})
    return frozenset(str(item) for item in value)


def _evidenced(spec: Any, name: str) -> bool:
    """The catalog entry carries evidence for ``name`` and, if the evidence names a model, it is this model."""
    evidence = getattr(spec, "evidence", None)
    proof = evidence.get(name) if isinstance(evidence, Mapping) else None
    if proof is None:
        return False
    named = getattr(proof, "model", None)
    return named is None or named == getattr(spec, "model", None)


@dataclass(frozen=True)
class ModelPrivacy:
    """What the catalog and the profile say about the model that would receive the task.

    ``cataloged`` is False when the model is not a catalog entry: then nothing is declared about it, ``host`` and
    ``retention_status`` are ``unknown`` and ``allowed_data_classes`` is None.
    """
    cataloged: bool = False
    provider: str | None = None
    host: str = "unknown"
    retention_status: str = "unknown"
    allowed_data_classes: frozenset[str] | None = None
    profile_data_classes: frozenset[str] | None = None
    provider_rejected: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_data_classes", _classes(self.allowed_data_classes))
        object.__setattr__(self, "profile_data_classes", _classes(self.profile_data_classes))

    @classmethod
    def from_spec(cls, spec: Any, *, profile_data_classes: Iterable[str] | None = None,
                  provider_rejected: bool = False) -> "ModelPrivacy":
        """From a ``catalog.ModelSpec`` (or None for a model the catalog does not describe)."""
        if spec is None:
            return cls(profile_data_classes=_classes(profile_data_classes), provider_rejected=provider_rejected)
        retention = getattr(spec, "retention", None)
        status = retention.get("status") if isinstance(retention, Mapping) else None
        host = getattr(spec, "host", None)
        # Contract section 2: a claim without evidence is ``unknown``, so a host or retention that nobody backed (or that
        # was backed for another model name) never opens ``restricted``.
        if not _evidenced(spec, "host"):
            host = "unknown"
        if not _evidenced(spec, "retention"):
            status = "unknown"
        return cls(cataloged=True, provider=getattr(spec, "provider", None),
                   host=host if host in HOSTS else "unknown",
                   retention_status=status if status in RETENTION_STATUSES else "unknown",
                   allowed_data_classes=getattr(spec, "allowed_data_classes", None),
                   profile_data_classes=_classes(profile_data_classes), provider_rejected=provider_rejected)


@dataclass(frozen=True)
class PrivacyDecision:
    allowed: bool
    reason: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def stop(self) -> bool:
        """A denial is a ``Stop`` (autonomy level 0), never an approval to wait for."""
        return not self.allowed

    @property
    def level(self) -> int | None:
        return None if self.allowed else STOP_LEVEL

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "reason": self.reason, "warnings": list(self.warnings),
                "action": "continue" if self.allowed else "stop", "level": self.level}


def _deny(reason: str, warnings: Iterable[str]) -> PrivacyDecision:
    return PrivacyDecision(False, reason, tuple(dict.fromkeys(warnings)))


def decide_privacy(data_class: str | None, privacy: ModelPrivacy | None) -> PrivacyDecision:
    """Whether a task of ``data_class`` may go to the model described by ``privacy``. Fails closed."""
    privacy = privacy if isinstance(privacy, ModelPrivacy) else ModelPrivacy()
    warnings: list[str] = []
    # Also for a model the catalog does not describe (every CLI model today): it is the most unknown case of all.
    if privacy.host == "unknown":
        warnings.append("model_host_unknown")
    if privacy.retention_status == "unknown":
        warnings.append("model_retention_unknown")
    if privacy.provider_rejected:
        return _deny(PROVIDER_REJECTED, warnings)
    declared = privacy.allowed_data_classes is not None or privacy.profile_data_classes is not None
    if data_class is None:
        if declared:
            return _deny(PRIVACY_CLASS_UNKNOWN, warnings)
        warnings.append("data_class_unresolved")
        return PrivacyDecision(True, None, tuple(dict.fromkeys(warnings)))
    if not isinstance(data_class, str) or data_class not in DATA_CLASSES:
        return _deny(PRIVACY_CLASS_UNKNOWN, warnings)
    if not declared:
        # A known class, but neither the model nor the profile lists any class: nothing authorizes it (fail closed).
        return _deny(PRIVACY_CLASS_UNKNOWN, warnings)
    if privacy.allowed_data_classes is not None and data_class not in privacy.allowed_data_classes:
        return _deny(DATA_CLASS_NOT_ALLOWED, warnings)
    if privacy.profile_data_classes is not None and data_class not in privacy.profile_data_classes:
        return _deny(DATA_CLASS_NOT_ALLOWED, warnings)
    if data_class == RESTRICTED:
        # Never outside the local host, whatever the profile allows; unknown host or retention is not a proof of local.
        if privacy.host != "local" or privacy.retention_status == "unknown":
            warnings.extend(("model_host_unknown",) if privacy.host == "unknown" else ())
            warnings.extend(("model_retention_unknown",) if privacy.retention_status == "unknown" else ())
            return _deny(DATA_CLASS_NOT_ALLOWED, warnings)
    return PrivacyDecision(True, None, tuple(dict.fromkeys(warnings)))


def combine(parts: Iterable[ModelPrivacy]) -> ModelPrivacy:
    """The most restrictive reading of several catalog entries that answer to one model name.

    Allowed classes are intersected, a host or retention that the entries do not agree on is ``unknown``, and the provider is
    rejected only when every entry is. An empty input describes nothing (not cataloged).
    """
    parts = list(parts)
    if not parts:
        return ModelPrivacy()
    allowed: frozenset[str] | None = None
    for part in parts:
        if part.allowed_data_classes is not None:
            allowed = part.allowed_data_classes if allowed is None else allowed & part.allowed_data_classes
    first = parts[0]
    same = lambda name: all(getattr(part, name) == getattr(first, name) for part in parts)  # noqa: E731
    return ModelPrivacy(
        cataloged=True, provider=first.provider if same("provider") else None,
        host=first.host if same("host") else "unknown",
        retention_status=first.retention_status if same("retention_status") else "unknown",
        allowed_data_classes=allowed, profile_data_classes=first.profile_data_classes,
        provider_rejected=all(part.provider_rejected for part in parts))


def resolve_model_privacy(models: Mapping[str, Any] | None, model: str | None, *, descriptor: Any = None,
                          profile_data_classes: Iterable[str] | None = None) -> ModelPrivacy:
    """What the catalog says about ``model`` (a catalog id, else a provider model name) for this executor.

    ``provider_rejected`` is the catalog's reading of the executor: the executor lists the models it takes (its
    ``model_support``) and does not list this one (the test of ``selection.model_exclusions``). What an adapter itself
    refuses (Copilot and a Claude model, ``refuses_model``) is not counted here: the adapter keeps its own code
    (``claude_model_rejected``) and refusal, before the task changes, exactly as before. A model the catalog does not
    describe gives a not-cataloged ``ModelPrivacy`` that still carries the profile's classes.
    """
    from .selection import NOT_DECLARED, model_exclusions
    table = models if isinstance(models, Mapping) else {}
    if not isinstance(model, str) or not model:
        return ModelPrivacy(profile_data_classes=_classes(profile_data_classes))
    key = model.casefold()
    matches = [spec for model_id, spec in table.items() if str(model_id).casefold() == key]
    if not matches:
        matches = [spec for spec in table.values()
                   if isinstance(getattr(spec, "model", None), str) and spec.model.casefold() == key]
    if not matches:
        return ModelPrivacy(profile_data_classes=_classes(profile_data_classes))
    excluded = model_exclusions(matches, descriptor) if descriptor is not None else {}
    excluded = {model_id for model_id, reason in excluded.items() if reason == NOT_DECLARED}
    return combine(ModelPrivacy.from_spec(spec, profile_data_classes=profile_data_classes,
                                          provider_rejected=str(getattr(spec, "id", "")) in excluded)
                   for spec in matches)
