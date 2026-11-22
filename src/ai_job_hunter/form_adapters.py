"""M7.3 : adaptateurs statiques, strictement autorisés par domaine/chemin.

Aucun adaptateur n'est déduit d'un contenu de page ni d'un LLM. Les noms de
champs et la provenance des données sont déclarés localement dans un TOML.
"""

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib

from ai_job_hunter.browser_preview import NavigationDenied, _canonical_url, _host

IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,79}\Z")
ADAPTER_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,79}\Z")
PREFIX = re.compile(r"/[A-Za-z0-9_/-]{0,255}\Z")
ANSWER = re.compile(r"answers\.[a-z][a-z0-9_]{1,59}\Z")
SOURCES = frozenset({
    "identity.full_name", "identity.email", "identity.city",
    "identity.phone", "identity.portfolio_url",
    "application.availability", "application.alternance_rhythm",
    "application.motivation",
})
TAGS = frozenset(("input", "textarea", "select"))
MAX_BYTES = 65536


@dataclass(frozen=True)
class FieldBinding:
    name: str
    tag: str
    source: str
    required: bool

    @property
    def selector(self) -> str:
        # name has been validated; safe to put in a quoted CSS attribute.
        return f'{self.tag}[name="{self.name}"]'


@dataclass(frozen=True)
class FormAdapter:
    id: str
    host: str
    path_prefix: str
    form_id: str
    permission_note: str
    fields: tuple[FieldBinding, ...]

    def accepts(self, url: str) -> bool:
        host, path, _ = _canonical_url(url)
        return (
            host == self.host
            and (self.path_prefix == "/" or path == self.path_prefix
                 or path.startswith(self.path_prefix + "/"))
        )


@dataclass(frozen=True)
class AdapterCatalog:
    adapters: tuple[FormAdapter, ...]

    @classmethod
    def load(cls, path: Path) -> "AdapterCatalog":
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise ValueError("Fichier d'adaptateurs absent, symbolique ou trop volumineux.")
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
        items = raw.get("adapter")
        if (not isinstance(raw, dict) or raw.keys() != {"adapter"}
                or not isinstance(items, list) or not 1 <= len(items) <= 30):
            raise ValueError("Déclarer 1 à 30 [[adapter]].")
        adapters = []
        seen = set()
        for item in items:
            expected = {"id", "host", "path_prefix", "form_id", "permission_note", "field"}
            if not isinstance(item, dict) or item.keys() != expected:
                raise ValueError("Adaptateur : propriétés attendues invalides.")
            adapter_id, form_id = item["id"], item["form_id"]
            if not isinstance(adapter_id, str) or not ADAPTER_ID.fullmatch(adapter_id):
                raise ValueError("Identifiant d'adaptateur invalide.")
            if not isinstance(form_id, str) or not IDENTIFIER.fullmatch(form_id):
                raise ValueError("form_id invalide.")
            host = _host(item["host"])
            prefix = item["path_prefix"]
            if (not isinstance(prefix, str) or not PREFIX.fullmatch(prefix)
                    or "//" in prefix or "/../" in prefix):
                raise ValueError("path_prefix invalide.")
            prefix = prefix.rstrip("/") or "/"
            note = item["permission_note"]
            if (not isinstance(note, str) or not 12 <= len(note.strip()) <= 300
                    or any(ord(ch) < 32 for ch in note)):
                raise ValueError("Justification d'autorisation obligatoire.")
            bindings = item["field"]
            if not isinstance(bindings, list) or not 1 <= len(bindings) <= 30:
                raise ValueError("Déclarer 1 à 30 [[adapter.field]].")
            found = set()
            parsed = []
            for raw_field in bindings:
                if not isinstance(raw_field, dict) or raw_field.keys() != {
                    "name", "tag", "source", "required",
                }:
                    raise ValueError("Champ adaptateur : propriétés manquantes ou inconnues.")
                name, tag, source, required = (
                    raw_field["name"], raw_field["tag"],
                    raw_field["source"], raw_field["required"],
                )
                if (not isinstance(name, str) or not IDENTIFIER.fullmatch(name)
                        or tag not in TAGS
                        or not isinstance(source, str)
                        or not (source in SOURCES or ANSWER.fullmatch(source))
                        or type(required) is not bool):
                    raise ValueError("Correspondance de champ invalide.")
                if name in found:
                    raise ValueError("Un nom de champ ne peut apparaître qu'une fois.")
                found.add(name)
                parsed.append(FieldBinding(name, tag, source, required))
            identity = (host, prefix)
            if adapter_id in seen or identity in seen:
                raise ValueError("Adaptateur ou chemin dupliqué.")
            seen.add(adapter_id)
            seen.add(identity)
            adapters.append(FormAdapter(
                adapter_id, host, prefix, form_id, note.strip(), tuple(parsed),
            ))
        return cls(tuple(adapters))

    def for_url(self, url: str) -> FormAdapter:
        matches = tuple(adapter for adapter in self.adapters if adapter.accepts(url))
        if len(matches) != 1:
            raise NavigationDenied("Aucun adaptateur unique et autorisé pour cette URL.")
        return matches[0]
