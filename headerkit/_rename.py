"""The ``rename_symbol`` and ``resolve_collision`` hook points.

Renaming a symbol is a language-agnostic transform; deciding whether two renamed
symbols are *the same identifier* is not. Everything here takes the target
language's identity function as a parameter, so the module holds no knowledge of
Nim, Python, or any other output language. The writers supply that function --
:func:`headerkit.writers.nim.nim_ident_identity` is the Nim one.

Two hook points are dispatched from here:

``rename_symbol``
    Waterfall. ``renamer(name: str, *, context: PipelineContext, kind: str) -> str``.
    Runs highest priority first, so a writer registering its language's legality
    rules at :attr:`~headerkit.hooks.Priority.FALLBACK` sees the name *after*
    every project renamer has had it. The grammar floor is therefore not
    bypassable by configuration.

``resolve_collision``
    First result. Returns a mapping from every collided symbol to its final
    identifier, or ``None`` to decline. The mapping is re-checked, never
    trusted: a resolver that answers a collision with another collision fails
    loudly.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from headerkit.hooks import HookDispatcher, HookRegistry, PipelineContext, Priority

#: The closed set of symbol kinds a renamer may be dispatched for.
#:
#: ``kind`` is what makes "change case" expressible at all: Nim spells types in
#: PascalCase and procs in camelCase, which is one rule per kind rather than one
#: rule for the module.
RENAME_KINDS: frozenset[str] = frozenset(
    {
        "function",
        "struct",
        "union",
        "enum",
        "enumerator",
        "field",
        "typedef",
        "param",
        "macro",
    }
)

#: Kinds that share one flat module-level namespace in the generated output, and
#: are therefore checked for collisions against each other.
#:
#: ``field`` and ``param`` are renamed like every other kind but are **not**
#: collision-checked, and that is a known gap rather than a safe exclusion. Nim
#: rejects a colliding field or parameter exactly as it rejects a colliding
#: module-level symbol -- an object declaring both ``fooBar`` and ``foo_bar`` is
#: ``attempt to redefine: 'foo_bar'``, and so is a proc taking both as
#: parameters. Detecting those needs the per-record and per-proc identity that
#: the IR contract work introduces, so until then a header whose collision is at
#: field or parameter level produces a module its compiler refuses, with no
#: diagnostic from headerkit. ``tests/test_rename.py`` pins both cases as strict
#: xfails so they go red the day that lands.
MODULE_SCOPE_KINDS: frozenset[str] = frozenset(
    {"function", "struct", "union", "enum", "enumerator", "typedef", "macro"}
)

CASE_STYLES: frozenset[str] = frozenset({"snake", "camel", "pascal", "preserve"})

COLLISION_POLICIES: frozenset[str] = frozenset(
    {
        "error",
        "suffix_header_stem",
        "prefer_shortest",
        "prefer_longest",
        "prefer_first_declared",
    }
)


class RenameError(ValueError):
    """A rename or a collision resolution could not be completed."""


class SymbolCollisionError(RenameError):
    """Two or more source symbols collapsed onto one target identifier."""


@dataclass(frozen=True, order=True)
class Symbol:
    """One source symbol being renamed.

    ``index`` is declaration order within the unit and ``header`` is the file the
    declaration came from; both exist so a collision policy can be deterministic
    without reading the IR.
    """

    index: int
    name: str
    kind: str
    header: str = ""

    @property
    def header_stem(self) -> str:
        """The originating header's filename without directories or extension."""
        return Path(self.header).stem if self.header else ""


_WORD_BOUNDARY = re.compile(r"[^0-9A-Za-z]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def split_words(name: str) -> list[str]:
    """Split an identifier into its words on separators and camel-case humps."""
    parts: list[str] = []
    for chunk in _WORD_BOUNDARY.split(name):
        if chunk:
            parts.extend(p for p in _CAMEL_BOUNDARY.split(chunk) if p)
    return parts


def apply_case(name: str, style: str) -> str:
    """Re-spell *name* in *style*, preserving any leading/trailing separators.

    The affixes are preserved rather than dropped because they carry meaning in C
    (``_reserved``, ``foo_``) and dropping them here would silently merge symbols
    that the caller never asked to merge. The language's own legality renamer,
    which runs last, is what deals with them.
    """
    if style not in CASE_STYLES:
        raise RenameError(f"unknown case style {style!r}; expected one of {sorted(CASE_STYLES)}")
    if style == "preserve" or not name:
        return name

    lead = name[: len(name) - len(name.lstrip("_"))]
    trail = name[len(name.rstrip("_")) :] if name.rstrip("_") else ""
    core = name[len(lead) : len(name) - len(trail)] if trail else name[len(lead) :]

    words = split_words(core)
    if not words:
        return name

    if style == "snake":
        body = "_".join(w.lower() for w in words)
    elif style == "pascal":
        body = "".join(w[:1].upper() + w[1:].lower() for w in words)
    else:  # camel
        first, *rest = words
        body = first.lower() + "".join(w[:1].upper() + w[1:].lower() for w in rest)
    return f"{lead}{body}{trail}"


def collapse_underscores(name: str) -> str:
    """Collapse every run of two or more underscores into a single underscore."""
    return re.sub(r"__+", "_", name)


@dataclass(frozen=True)
class RenameRule:
    """One declarative rename step, optionally restricted to certain kinds.

    Steps are applied in the order written: ``strip_prefix``, ``case``,
    ``collapse_underscores``, ``add_prefix``, ``add_suffix``. The order is fixed
    rather than configurable so that two configs that list the same fields mean
    the same thing.
    """

    kinds: tuple[str, ...] = ()
    strip_prefix: str | None = None
    add_prefix: str | None = None
    add_suffix: str | None = None
    case: str = "preserve"
    collapse_underscores: bool = False

    def __post_init__(self) -> None:
        unknown = sorted(set(self.kinds) - RENAME_KINDS)
        if unknown:
            raise RenameError(f"unknown rename kind(s) {unknown}; expected from {sorted(RENAME_KINDS)}")
        if self.case not in CASE_STYLES:
            raise RenameError(f"unknown case style {self.case!r}; expected one of {sorted(CASE_STYLES)}")

    def applies_to(self, kind: str) -> bool:
        return not self.kinds or kind in self.kinds

    def apply(self, name: str) -> str:
        out = name
        if self.strip_prefix and out.startswith(self.strip_prefix):
            out = out[len(self.strip_prefix) :]
        out = apply_case(out, self.case)
        if self.collapse_underscores:
            out = collapse_underscores(out)
        if self.add_prefix:
            out = f"{self.add_prefix}{out}"
        if self.add_suffix:
            out = f"{out}{self.add_suffix}"
        return out

    def as_dict(self) -> dict[str, object]:
        return {
            "kinds": list(self.kinds),
            "strip_prefix": self.strip_prefix,
            "add_prefix": self.add_prefix,
            "add_suffix": self.add_suffix,
            "case": self.case,
            "collapse_underscores": self.collapse_underscores,
        }


@dataclass(frozen=True)
class RenameConfig:
    """The declarative half of symbol renaming, as loaded from config."""

    rules: tuple[RenameRule, ...] = ()
    collision_policy: str = "error"

    def __post_init__(self) -> None:
        if self.collision_policy not in COLLISION_POLICIES:
            raise RenameError(
                f"unknown collision policy {self.collision_policy!r}; expected one of {sorted(COLLISION_POLICIES)}"
            )

    @property
    def is_default(self) -> bool:
        """True when this config asks for nothing beyond headerkit's defaults."""
        return not self.rules and self.collision_policy == "error"

    def apply(self, name: str, kind: str) -> str:
        out = name
        for rule in self.rules:
            if rule.applies_to(kind):
                out = rule.apply(out)
        return out

    def fingerprint(self) -> str:
        """A stable digest of this config, for the output cache key.

        Rename configuration changes generated output, so it has to be part of
        the key. Without it, editing a rename rule serves the previously cached
        output under the old names and reports a hit.
        """
        payload = json.dumps(
            {
                "rules": [rule.as_dict() for rule in self.rules],
                "collision_policy": self.collision_policy,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


DEFAULT_RENAME_CONFIG = RenameConfig()


def parse_rename_config(data: Mapping[str, object], source: object = "<config>") -> RenameConfig:
    """Build a :class:`RenameConfig` from a parsed ``[rename]`` config table."""
    policy = data.get("collision_policy", "error")
    if not isinstance(policy, str):
        raise RenameError(f"headerkit: config error in {source}: rename.collision_policy must be str")
    if policy not in COLLISION_POLICIES:
        raise RenameError(
            f"headerkit: config error in {source}: rename.collision_policy must be one of "
            f"{sorted(COLLISION_POLICIES)}, got {policy!r}"
        )

    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        raise RenameError(f"headerkit: config error in {source}: rename.rules must be an array of tables")

    rules: list[RenameRule] = []
    for entry in cast(list[object], raw_rules):
        if not isinstance(entry, dict):
            raise RenameError(f"headerkit: config error in {source}: each rename.rules entry must be a table")
        table = cast(dict[str, object], entry)
        unknown = sorted(
            set(table) - {"kinds", "strip_prefix", "add_prefix", "add_suffix", "case", "collapse_underscores"}
        )
        if unknown:
            raise RenameError(f"headerkit: config error in {source}: unknown rename rule key(s) {unknown}")
        kinds_val = table.get("kinds", [])
        if not isinstance(kinds_val, list) or not all(isinstance(k, str) for k in cast(list[object], kinds_val)):
            raise RenameError(f"headerkit: config error in {source}: rename rule 'kinds' must be list[str]")
        collapse = table.get("collapse_underscores", False)
        if not isinstance(collapse, bool):
            raise RenameError(f"headerkit: config error in {source}: rename rule 'collapse_underscores' must be bool")
        str_fields: dict[str, str | None] = {}
        for key in ("strip_prefix", "add_prefix", "add_suffix", "case"):
            val = table.get(key)
            if val is not None and not isinstance(val, str):
                raise RenameError(f"headerkit: config error in {source}: rename rule {key!r} must be str")
            str_fields[key] = val
        rules.append(
            RenameRule(
                kinds=tuple(cast(list[str], kinds_val)),
                strip_prefix=str_fields["strip_prefix"],
                add_prefix=str_fields["add_prefix"],
                add_suffix=str_fields["add_suffix"],
                case=str_fields["case"] or "preserve",
                collapse_underscores=collapse,
            )
        )
    return RenameConfig(rules=tuple(rules), collision_policy=policy)


def dispatch_rename(
    name: str,
    *,
    kind: str,
    context: PipelineContext,
    dispatcher: HookDispatcher | None = None,
) -> str:
    """Run the ``rename_symbol`` waterfall over *name*."""
    if kind not in RENAME_KINDS:
        raise RenameError(f"unknown rename kind {kind!r}; expected one of {sorted(RENAME_KINDS)}")
    disp = dispatcher or HookDispatcher()
    result = disp.waterfall("rename_symbol", name, context=context, kind=kind)
    if not isinstance(result, str) or not result:
        raise RenameError(
            f"a rename_symbol hook returned {result!r} for {name!r} (kind={kind}); expected a non-empty str"
        )
    return result


def _slug(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum())


def _policy_resolver(
    policy: str,
) -> Callable[[Sequence[Symbol], str], dict[Symbol, str]] | None:
    """Return the mapping function for a named declarative collision policy.

    ``error`` has no resolver: declining is what makes the default a raise.

    Every policy here is deterministic given the same input set -- each sorts
    explicitly rather than relying on the iteration order it was handed -- and
    every one of them is re-checked afterwards like any other resolver. A policy
    that cannot separate the members it was given (``suffix_header_stem`` on two
    symbols from one header, for instance) produces a collision and the
    re-check raises, which is the honest outcome: appending a header stem does
    not distinguish two declarations in the same header.
    """
    if policy == "error":
        return None

    if policy == "suffix_header_stem":

        def by_stem(collided: Sequence[Symbol], target: str) -> dict[Symbol, str]:
            return {sym: f"{target}_{_slug(sym.header_stem)}" if sym.header_stem else target for sym in collided}

        return by_stem

    keys: dict[str, Callable[[Symbol], object]] = {
        "prefer_shortest": lambda s: (len(s.name), s.index),
        "prefer_longest": lambda s: (-len(s.name), s.index),
        "prefer_first_declared": lambda s: (s.index,),
    }
    key = keys[policy]

    def by_preference(collided: Sequence[Symbol], target: str) -> dict[Symbol, str]:
        ordered = sorted(collided, key=key)  # type: ignore[arg-type]
        winner, *losers = ordered
        mapping = {winner: target}
        for sym in losers:
            mapping[sym] = f"{target}_{_slug(sym.name)}"
        return mapping

    return by_preference


#: The hook points whose registrations change generated names, and therefore
#: change generated output.
RENAME_HOOK_POINTS: tuple[str, ...] = ("rename_symbol", "resolve_collision")


def rename_cache_fingerprint() -> str:
    """Digest the registered renaming hooks, for the output cache key.

    Renaming changes generated output, so a cache key that ignores it serves the
    previous output under the previous names and reports a hit -- silently, with
    no diagnostic anywhere. The digest therefore covers every registered
    ``rename_symbol`` and ``resolve_collision`` implementation.

    A hook built from declarative config carries its config's own digest on
    ``headerkit_rename_fingerprint``, so two different rename configs cannot
    share a key. A hook written in Python is identified by module and qualified
    name: editing that function's body does not move the key, which is the same
    limitation writer plugins already have with ``cache_version``.

    The parts are sorted so that the digest does not depend on the order writer
    modules happened to be imported in.
    """
    parts: list[str] = []
    for impl in HookRegistry.snapshot():
        if impl.point not in RENAME_HOOK_POINTS:
            continue
        fn = impl.func
        ident = getattr(fn, "headerkit_rename_fingerprint", None) or f"{fn.__module__}.{fn.__qualname__}"
        matchers = ",".join(f"{k}={v}" for k, v in sorted(impl.matchers.items()))
        parts.append(f"{impl.point}|{impl.priority}|{matchers}|{ident}")
    return hashlib.sha256("\0".join(sorted(parts)).encode("utf-8")).hexdigest()


def register_config_hooks(config: RenameConfig, **matchers: str) -> None:
    """Register the declarative rename config as hook implementations.

    Registered at :attr:`~headerkit.hooks.Priority.PROJECT`, above every writer's
    legality renamer at ``FALLBACK``. The waterfall runs highest priority first,
    so a project rule is applied and then made legal, never the reverse.

    Registering the same config twice is a no-op. It was not, and the second
    registration was not merely redundant: ``rename_symbol`` is a waterfall, so
    two copies of a prefix rule ran in series and ``foo`` came out ``hkhkfoo``.
    The output cache key digests the registered hooks, so the duplicate also
    moved the fingerprint and turned every subsequent lookup into a miss. Neither
    reported anything. Two ``main()`` calls in one process is enough to reach it,
    which is what a library consumer and the test suite both do.
    """
    if config.is_default:
        return
    fingerprint = config.fingerprint()
    if config.rules and not _already_registered("rename_symbol", fingerprint, matchers):
        renamer = make_config_renamer(config)
        renamer.headerkit_rename_fingerprint = fingerprint  # type: ignore[attr-defined]
        HookRegistry.register_global("rename_symbol", renamer, priority=Priority.PROJECT, **matchers)
    resolver = make_config_resolver(config)
    if resolver is not None and not _already_registered("resolve_collision", fingerprint, matchers):
        resolver.headerkit_rename_fingerprint = fingerprint  # type: ignore[attr-defined]
        HookRegistry.register_global("resolve_collision", resolver, priority=Priority.PROJECT, **matchers)


def _already_registered(point: str, fingerprint: str, matchers: Mapping[str, str]) -> bool:
    """Whether this exact config is already registered at ``point`` for these matchers.

    Identified by the config's fingerprint rather than by the function object: a
    second call builds a different closure for the same config, so identity would
    never match and the check would never fire.
    """
    return any(
        impl.point == point
        and getattr(impl.func, "headerkit_rename_fingerprint", None) == fingerprint
        and impl.matchers == dict(matchers)
        for impl in HookRegistry.snapshot()
    )


def make_config_resolver(
    config: RenameConfig,
) -> Callable[..., dict[Symbol, str] | None] | None:
    """Build a ``resolve_collision`` hook implementation from declarative config."""
    mapper = _policy_resolver(config.collision_policy)
    if mapper is None:
        return None

    def resolver(
        collided: Sequence[Symbol],
        target: str,
        *,
        context: PipelineContext,  # noqa: ARG001 -- part of the hook signature
        **_: object,
    ) -> dict[Symbol, str] | None:
        return mapper(collided, target)

    return resolver


def make_config_renamer(config: RenameConfig) -> Callable[..., str]:
    """Build a ``rename_symbol`` hook implementation from declarative config."""

    def renamer(name: str, *, context: PipelineContext, kind: str, **_: object) -> str:  # noqa: ARG001
        return config.apply(name, kind)

    return renamer


def _describe(symbols: Iterable[Symbol]) -> str:
    return ", ".join(f"{s.name!r} ({s.kind})" for s in sorted(symbols))


def _group_by_identity(assigned: Mapping[Symbol, str], identity: Callable[[str], str]) -> dict[str, list[Symbol]]:
    groups: dict[str, list[Symbol]] = {}
    for sym in sorted(assigned):
        groups.setdefault(identity(assigned[sym]), []).append(sym)
    return groups


def enforce_injectivity(
    assigned: Mapping[Symbol, str],
    *,
    identity: Callable[[str], str],
    context: PipelineContext,
    validate: Callable[[str], None] | None = None,
    dispatcher: HookDispatcher | None = None,
) -> dict[Symbol, str]:
    """Check that no two symbols share an identifier under *identity*.

    On a collision the ``resolve_collision`` hook is offered the group. The hook
    must return an identifier for **every** member -- a partial mapping is a
    refusal to decide about the rest, and keeping the original name for an
    uncovered symbol is the exact defect this exists to prevent. The returned
    identifiers are then re-validated and re-checked for collisions against the
    whole assignment; a resolver that answers a collision with another collision
    raises rather than being trusted.

    :raises SymbolCollisionError: when a collision is left unresolved.
    :raises RenameError: when a resolver returns an unusable mapping.
    """
    disp = dispatcher or HookDispatcher()
    result: dict[Symbol, str] = dict(assigned)

    for ident, group in sorted(_group_by_identity(result, identity).items()):
        if len(group) < 2:
            continue
        target = result[group[0]]
        mapping = disp.first_result(
            "resolve_collision",
            tuple(group),
            target,
            context=context,
        )
        if mapping is None:
            raise SymbolCollisionError(
                f"symbols {_describe(group)} all collapse to the identifier {target!r} "
                f"(identity {ident!r}) in the generated output. headerkit will not guess which "
                "one you meant: rename one at the source, add a rename_symbol rule, or register "
                "a resolve_collision hook. Note that this check covers module-scope symbols only; "
                "collisions between two fields of one record, or two parameters of one proc, are "
                "not detected yet and will surface as a compiler error instead."
            )
        if not isinstance(mapping, Mapping):
            raise RenameError(
                f"a resolve_collision hook returned {type(mapping).__name__} for {target!r}; expected a mapping"
            )
        typed = cast(Mapping[Symbol, str], mapping)
        missing = [sym for sym in group if sym not in typed]
        if missing:
            raise RenameError(
                f"a resolve_collision hook returned a partial mapping for {target!r}: "
                f"no identifier for {_describe(missing)}. Every collided symbol must be named."
            )
        extra = [sym for sym in typed if sym not in group]
        if extra:
            raise RenameError(
                f"a resolve_collision hook returned identifiers for symbols it was not asked about: {_describe(extra)}"
            )
        for sym in group:
            new_name = typed[sym]
            if not isinstance(new_name, str) or not new_name:
                raise RenameError(
                    f"a resolve_collision hook returned {new_name!r} for {sym.name!r}; expected a non-empty str"
                )
            if validate is not None:
                validate(new_name)
            result[sym] = new_name

    residual = {ident: group for ident, group in _group_by_identity(result, identity).items() if len(group) > 1}
    if residual:
        ident, group = sorted(residual.items())[0]
        raise SymbolCollisionError(
            f"after collision resolution, symbols {_describe(group)} still share the identifier "
            f"{result[group[0]]!r} (identity {ident!r}). A resolver must return identifiers that are "
            "pairwise distinct under the target language's identity rules."
        )
    return result
