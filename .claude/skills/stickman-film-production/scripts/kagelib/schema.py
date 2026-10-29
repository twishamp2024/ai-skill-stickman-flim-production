"""Stdlib-only JSON Schema (draft 2020-12 subset) validator.

Supports the keywords used by the Kage schemas: type, enum, const, properties,
required, additionalProperties, patternProperties, items, prefixItems,
minItems, maxItems, uniqueItems, minimum, maximum, exclusiveMinimum,
exclusiveMaximum, minLength, maxLength, pattern, propertyNames, allOf, anyOf, oneOf, not,
if/then/else, $ref (local and cross-file) and $defs. If the third-party
`jsonschema` package is installed, tests cross-check results against it.
"""
from __future__ import annotations

import re
from pathlib import Path

from .util import SCHEMA_DIR, load_json

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool) or (isinstance(v, float) and v.is_integer()),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}

SUPPORTED = {"$schema", "$id", "$ref", "$defs", "$comment", "title", "description", "default", "examples", "type",
             "enum", "const", "properties", "required", "additionalProperties", "patternProperties", "items",
             "prefixItems", "minItems", "maxItems", "uniqueItems", "minimum", "maximum", "exclusiveMinimum",
             "exclusiveMaximum", "minLength", "maxLength", "pattern", "allOf", "anyOf", "oneOf", "not", "if", "then",
             "else", "format", "minProperties", "deprecated", "readOnly", "propertyNames"}


class SchemaStore:
    def __init__(self, schema_dir: Path = SCHEMA_DIR):
        self.dir = Path(schema_dir)
        self.cache: dict[str, dict] = {}

    def get(self, name: str) -> dict:
        if name not in self.cache:
            self.cache[name] = load_json(self.dir / name)
        return self.cache[name]

    def resolve(self, ref: str, base: str) -> tuple[dict, str]:
        file, _, pointer = ref.partition("#")
        doc_name = file or base
        node = self.get(doc_name)
        for part in [p for p in pointer.split("/") if p]:
            node = node[part.replace("~1", "/").replace("~0", "~")]
        return node, doc_name


def validate(instance, schema_name: str, store: SchemaStore | None = None) -> list[str]:
    """Return a list of human-readable errors ("" path = document root)."""
    store = store or SchemaStore()
    errors: list[str] = []
    _check(instance, store.get(schema_name), "$", schema_name, store, errors)
    return errors


def _check(v, s, path, base, store, errors):
    if s is True or s == {}:
        return
    if s is False:
        errors.append(f"{path}: not allowed")
        return
    unknown = set(s) - SUPPORTED
    if unknown:
        raise ValueError(f"schema {base} uses unsupported keywords {sorted(unknown)}")
    if "$ref" in s:
        target, doc = store.resolve(s["$ref"], base)
        _check(v, target, path, doc, store, errors)
    if "type" in s:
        types = s["type"] if isinstance(s["type"], list) else [s["type"]]
        if not any(_TYPES[t](v) for t in types):
            errors.append(f"{path}: expected {'/'.join(types)}, got {type(v).__name__}")
            return
    if "enum" in s and v not in s["enum"]:
        errors.append(f"{path}: {v!r} not one of {s['enum']}")
    if "const" in s and v != s["const"]:
        errors.append(f"{path}: must equal {s['const']!r}")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if "minimum" in s and v < s["minimum"]:
            errors.append(f"{path}: {v} < minimum {s['minimum']}")
        if "maximum" in s and v > s["maximum"]:
            errors.append(f"{path}: {v} > maximum {s['maximum']}")
        if "exclusiveMinimum" in s and v <= s["exclusiveMinimum"]:
            errors.append(f"{path}: {v} must be > {s['exclusiveMinimum']}")
        if "exclusiveMaximum" in s and v >= s["exclusiveMaximum"]:
            errors.append(f"{path}: {v} must be < {s['exclusiveMaximum']}")
    if isinstance(v, str):
        if "minLength" in s and len(v) < s["minLength"]:
            errors.append(f"{path}: shorter than {s['minLength']}")
        if "maxLength" in s and len(v) > s["maxLength"]:
            errors.append(f"{path}: longer than {s['maxLength']}")
        if "pattern" in s and not re.search(s["pattern"], v):
            errors.append(f"{path}: {v!r} does not match {s['pattern']}")
    if isinstance(v, list):
        if "minItems" in s and len(v) < s["minItems"]:
            errors.append(f"{path}: needs at least {s['minItems']} items")
        if "maxItems" in s and len(v) > s["maxItems"]:
            errors.append(f"{path}: at most {s['maxItems']} items")
        if s.get("uniqueItems"):
            seen = []
            for item in v:
                if item in seen:
                    errors.append(f"{path}: duplicate item {item!r}")
                    break
                seen.append(item)
        prefix = s.get("prefixItems", [])
        for i, item in enumerate(v):
            if i < len(prefix):
                _check(item, prefix[i], f"{path}[{i}]", base, store, errors)
            elif "items" in s:
                _check(item, s["items"], f"{path}[{i}]", base, store, errors)
    if isinstance(v, dict):
        if "minProperties" in s and len(v) < s["minProperties"]:
            errors.append(f"{path}: needs at least {s['minProperties']} properties")
        for r in s.get("required", []):
            if r not in v:
                errors.append(f"{path}: missing required property '{r}'")
        if "propertyNames" in s:
            for k in v:
                _check(k, s["propertyNames"], f"{path}.<key {k!r}>", base, store, errors)
        props = s.get("properties", {})
        pats = s.get("patternProperties", {})
        for k, item in v.items():
            matched = False
            if k in props:
                matched = True
                _check(item, props[k], f"{path}.{k}", base, store, errors)
            for pat, sub in pats.items():
                if re.search(pat, k):
                    matched = True
                    _check(item, sub, f"{path}.{k}", base, store, errors)
            if not matched and "additionalProperties" in s:
                ap = s["additionalProperties"]
                if ap is False:
                    errors.append(f"{path}: unexpected property '{k}'")
                else:
                    _check(item, ap, f"{path}.{k}", base, store, errors)
    for sub in s.get("allOf", []):
        _check(v, sub, path, base, store, errors)
    if "anyOf" in s:
        if not any(not _sub_errors(v, sub, path, base, store) for sub in s["anyOf"]):
            errors.append(f"{path}: does not match any allowed form")
    if "oneOf" in s:
        n = sum(1 for sub in s["oneOf"] if not _sub_errors(v, sub, path, base, store))
        if n != 1:
            errors.append(f"{path}: must match exactly one form (matched {n})")
    if "not" in s and not _sub_errors(v, s["not"], path, base, store):
        errors.append(f"{path}: matches a forbidden form")
    if "if" in s:
        branch = "then" if not _sub_errors(v, s["if"], path, base, store) else "else"
        if branch in s:
            _check(v, s[branch], path, base, store, errors)


def _sub_errors(v, s, path, base, store):
    errs: list[str] = []
    _check(v, s, path, base, store, errs)
    return errs
