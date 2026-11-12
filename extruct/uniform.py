# mypy: disallow_untyped_defs=False
import copy
from typing import Any
from urllib.parse import urljoin, urlparse

from extruct.dublincore import get_lower_attrib

DEFAULT_SCHEMA_CONTEXT = "http://schema.org"

# Property names that always hold (or may hold) an URL or a list of URLs.
# Matched case-insensitively, both on the full name and on its last segment
# after a ":" ("og:image" -> "image") and before any "Url"/"URL" suffix.
_URL_KEY_TOKENS = (
    "url",
    "image",
    "photo",
    "logo",
    "icon",
    "thumbnail",
    "screenshot",
    "link",
    "href",
    "src",
    "uri",
    "sameas",
    "embedurl",
    "contenturl",
    "downloadurl",
    "relatedlink",
    "discussionurl",
    "mainentityofpage",
    "isbasedon",
    "workexample",
)


def _is_url_key(key):
    """Whether a property name may contain an URL value."""
    if not isinstance(key, str):
        return False
    normalized = key.rsplit(":", 1)[-1].lower()
    if normalized in _URL_KEY_TOKENS:
        return True
    return normalized.endswith(("url", "uri"))


def _is_absolute_url(value):
    """Whether ``value`` needs no resolution against the document base."""
    if not isinstance(value, str):
        return True
    if not value.strip():
        return True
    head = value.lstrip()[:5].lower()
    # protocol-relative URLs and URIs carrying a scheme stay untouched;
    # bare fragments (e.g. "#frag") are resolved via urljoin
    return (
        value.startswith("//")
        or "://" in head
        or head.startswith(("data:", "mailto:", "tel:", "javascript:"))
    )


def _resolve_url(value, base_url):
    """Resolve a possibly relative URL against the document base."""
    if base_url is None or _is_absolute_url(value):
        return value
    return urljoin(base_url, value)


def _resolve_urls(value, base_url):
    """Walk an extracted structure and resolve its URL values in place."""
    if isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = _resolve_urls(item, base_url)
    elif isinstance(value, dict):
        for key, item in value.items():
            if _is_url_key(key):
                value[key] = _resolve_url_field(item, base_url)
            else:
                value[key] = _resolve_urls(item, base_url)
    return value


def _resolve_url_field(item, base_url):
    """Resolve the value(s) of an URL-typed property.

    Strings are joined to the base; a nested node such as an ImageObject is
    walked recursively so its own URL fields are resolved, and an
    ``{"value": url}`` object (as produced by microformats) resolves its
    ``value``.
    """
    if isinstance(item, str):
        return _resolve_url(item, base_url)
    if isinstance(item, list):
        return [_resolve_url_field(element, base_url) for element in item]
    if isinstance(item, dict):
        if isinstance(item.get("value"), str):
            item = dict(item)
            item["value"] = _resolve_url(item["value"], base_url)
            return item
        return _resolve_urls(item, base_url)
    return item


def _split_type(typ, schema_context):
    """Align an absolute ``@type`` URL with its context, like infer_context."""
    if not isinstance(typ, str):
        return schema_context, typ
    parsed = urlparse(typ)
    if not parsed.netloc:
        return schema_context, typ
    context, short = infer_context(typ, schema_context)
    return context, short


def _normalize_jsonld_node(node, base_url, parent_context=DEFAULT_SCHEMA_CONTEXT):
    """Recursively normalize one JSON-LD object into a flat uniform item.

    Returns a list when the node holds an ``@graph`` (its nodes are split),
    and a bare scalar when the node is an ``@value`` value object. The
    inherited context is used for type alignment but only stamped onto the
    output when explicitly declared (top-level defaults and graph nodes are
    handled by :func:`_ujsonld`), mirroring the microdata uniform convention
    where nested items repeat ``@type`` but not ``@context``.
    """
    if isinstance(node, list):
        normalized = []
        for item in node:
            result = _normalize_jsonld_node(item, base_url, parent_context)
            if isinstance(result, list):
                normalized.extend(result)
            else:
                normalized.append(result)
        return normalized
    if not isinstance(node, dict):
        return node
    if "@value" in node:
        # bare value object: unpack it, dropping @type/@language annotations
        return node["@value"]
    if set(node) <= {"@id", "@index"}:
        # node reference: keep it a pure reference, only resolve the @id
        reference = dict(node)
        if "@id" in reference:
            reference["@id"] = _resolve_url(reference["@id"], base_url)
        return reference
    if "@graph" in node:
        context = node.get("@context", parent_context)
        # nested graph nodes are ordinary nested items: they use the context
        # for type alignment but do not repeat it (only top-level items do)
        return _normalize_jsonld_node(list(node["@graph"]), base_url, context)

    context = node.get("@context", parent_context)
    out: dict[Any, Any] = {}
    if "@context" in node:
        out["@context"] = node["@context"]
    if "@id" in node:
        out["@id"] = _resolve_url(node["@id"], base_url)

    if "@type" in node:
        typ = node["@type"]
        if isinstance(typ, list):
            aligned = [_split_type(t, context) for t in typ]
            contexts = {ctx for ctx, _ in aligned}
            if len(contexts) == 1 and aligned[0][1] != typ[0]:
                out["@type"] = [short for _, short in aligned]
            else:
                # non-URL types or types from different vocabularies: keep them
                out["@type"] = typ
        else:
            _, short = _split_type(typ, context)
            out["@type"] = short

    for key, value in node.items():
        if key in ("@context", "@type", "@graph", "@id"):
            continue
        out[key] = _normalize_jsonld_node(value, base_url, context)

    return _resolve_urls(out, base_url)


def _ujsonld(extracted, base_url):
    """Uniform JSON-LD: split @graph, unpack @value, align @type, resolve URLs."""
    top_level: list[Any] = []
    for node in extracted:
        if isinstance(node, dict) and "@graph" in node:
            # a top-level graph is split into independent top-level items,
            # each inheriting the graph's context
            context = node.get("@context", DEFAULT_SCHEMA_CONTEXT)
            for graph_node in node["@graph"]:
                if isinstance(graph_node, dict) and "@context" not in graph_node:
                    top_level.append({**graph_node, "@context": context})
                else:
                    top_level.append(graph_node)
        elif isinstance(node, dict) and "@context" not in node:
            top_level.append({**node, "@context": DEFAULT_SCHEMA_CONTEXT})
        else:
            top_level.append(node)
    normalized = _normalize_jsonld_node(top_level, base_url)
    if isinstance(normalized, dict):
        return [normalized]
    return normalized


def _uopengraph(extracted, base_url=None, with_og_array=False):
    out = []
    for obj in extracted:
        # In order of appearance in the page
        properties = list(obj["properties"])
        flattened: dict[Any, Any] = {}

        for k, v in properties:
            if k not in flattened.keys():
                flattened[k] = v
            elif v and v.strip():
                # If og_array isn't required add first non empty value
                if not with_og_array:
                    if not flattened[k] or not flattened[k].strip():
                        flattened[k] = v
                else:
                    if isinstance(flattened[k], list):
                        flattened[k].append(v)
                    elif flattened[k] and flattened[k].strip():
                        flattened[k] = [flattened[k], v]
                    else:
                        flattened[k] = v

        t = flattened.pop("og:type", None)
        if t:
            flattened["@type"] = t
        flattened["@context"] = obj["namespace"]
        out.append(flattened)
    if base_url is not None:
        for item in out:
            _resolve_urls(item, base_url)
    return out


def _umicrodata_microformat(extracted, schema_context, base_url=None):
    res = []
    if isinstance(extracted, list):
        for obj in extracted:
            res.append(flatten_dict(obj, schema_context, True, base_url))
    elif isinstance(extracted, dict):
        res.append(flatten_dict(extracted, schema_context, False, base_url))
    return res


def _udublincore(extracted):
    out = []
    extracted_cpy = copy.deepcopy(extracted)
    for obj in extracted_cpy:
        context = obj.pop("namespaces", None)
        obj["@context"] = context
        elements = obj["elements"]
        for element in elements:
            for key, value in element.items():
                if get_lower_attrib(value) == "type":
                    obj["@type"] = element["content"]
                    obj["elements"].remove(element)
                    break
        out.append(obj)
    return out


def _flatten(element, schema_context, base_url=None):
    if isinstance(element, dict):
        element = flatten_dict(element, schema_context, False, base_url)
    elif isinstance(element, list):
        element = [
            (
                flatten_dict(o, schema_context, False, base_url)
                if isinstance(o, dict)
                else o
            )
            for o in element
        ]
    return element


def flatten_dict(d, schema_context, add_context, base_url=None):
    out = dict(d)
    typ = out.pop("type", None)
    if not typ:
        # An itemscope without itemtype: still flatten its properties instead
        # of leaving the raw "properties"/"value" wrappers in place.
        props = out.pop("properties", None)
        if props is not None:
            for field, value in props.items():
                out[field] = _flatten(value, schema_context, base_url)
        if add_context:
            out["@context"] = schema_context
        if base_url is not None:
            _resolve_urls(out, base_url)
        return out

    if isinstance(typ, list):
        aligned = [_split_type(t, schema_context) for t in typ]
        contexts = {ctx for ctx, _ in aligned}
        if len(contexts) == 1 and aligned[0][1] != typ[0]:
            context = aligned[0][0]
            out["@type"] = [short for _, short in aligned]
        else:
            # non-URL types (e.g. microformats) or mixed vocabularies
            context = schema_context
            out["@type"] = typ
    else:
        context, typ = infer_context(typ, schema_context)
        out["@type"] = typ

    if add_context:
        out["@context"] = context

    props = out.pop("properties", {})
    for field, value in props.items():
        value = _flatten(value, schema_context, base_url)
        out[field] = value

    children = out.pop("children", [])
    if children:
        out["children"] = []
    for child in children:
        child = _flatten(child, schema_context, base_url)
        out["children"].append(child)
    if base_url is not None:
        _resolve_urls(out, base_url)
    return out


def infer_context(typ, context=DEFAULT_SCHEMA_CONTEXT):
    parsed_context = urlparse(typ)
    if parsed_context.netloc:
        base = "".join([parsed_context.scheme, "://", parsed_context.netloc])
        if parsed_context.path and parsed_context.fragment:
            context = urljoin(base, parsed_context.path)
            typ = parsed_context.fragment.strip("/")
        elif parsed_context.path:
            context = base
            typ = parsed_context.path.strip("/")
    return context, typ
