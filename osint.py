from __future__ import annotations

import argparse
import concurrent.futures
import html
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

try:
    from ddgs import DDGS
except ImportError:
    DDGS = None


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36 "
    "OSINT-Public-Provider-Runner/3.0"
)

EMAIL_RE = re.compile(
    r"(?<![\w.+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])",
    re.I,
)
PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?55[\s.-]?)?(?:\(?\d{2}\)?[\s.-]?)?"
    r"(?:9\d{4}|[2-8]\d{3})[\s.-]?\d{4}(?!\d)"
)
CPF_CANDIDATE_RE = re.compile(
    r"(?<!\d)(\d{3}\s*\.?\s*\d{3}\s*\.?\s*\d{3}\s*-?\s*\d{2})(?!\d)"
)
ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)

SOCIAL_HOSTS = {
    "linkedin.com", "github.com", "gitlab.com", "instagram.com", "facebook.com",
    "x.com", "twitter.com", "youtube.com", "tiktok.com", "stackoverflow.com",
    "orcid.org",
}


@dataclass(frozen=True)
class SearchCriteria:
    name: str | None
    cpf: str | None

    @property
    def mode(self) -> str:
        if self.name and self.cpf:
            return "nome+cpf"
        if self.cpf:
            return "cpf"
        return "nome"

    @property
    def cpf_formatted(self) -> str | None:
        return format_cpf(self.cpf) if self.cpf else None


@dataclass
class ProviderRecord:
    provider: str
    provider_name: str
    title: str
    url: str | None
    data: dict[str, Any]
    matched_name: bool
    matched_cpf: bool


@dataclass
class ProviderResult:
    provider: str
    provider_name: str
    kind: str
    ok: bool
    status: str
    records: list[ProviderRecord]
    errors: list[str]


@dataclass
class PageInfo:
    url: str
    title: str | None
    description: str | None
    emails: list[str]
    phones: list[str]
    social_links: list[str]
    matched_name: bool
    matched_cpf: bool
    matched_all: bool
    error: str | None = None


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def exact_name_match(name: str, text: str) -> bool:
    target = normalize_text(name)
    haystack = normalize_text(text)
    return bool(target and haystack and f" {target} " in f" {haystack} ")


def exact_name_equal(name: str, candidate: str) -> bool:
    return bool(normalize_text(name)) and normalize_text(name) == normalize_text(candidate)


def normalize_cpf(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) != 11:
        raise ValueError("CPF deve conter exatamente 11 dígitos.")
    return digits


def format_cpf(cpf: str | None) -> str | None:
    if not cpf:
        return None
    digits = re.sub(r"\D", "", cpf)
    if len(digits) != 11:
        return None
    return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"


def mask_cpf(cpf: str | None) -> str | None:
    if not cpf:
        return None
    digits = re.sub(r"\D", "", cpf)
    if len(digits) != 11:
        return "***"
    return f"***.{digits[3:6]}.{digits[6:9]}-**"


def cpf_valid(cpf: str) -> bool:
    digits = re.sub(r"\D", "", cpf)
    if len(digits) != 11 or digits == digits[0] * 11:
        return False

    def calc(base: str, weight_start: int) -> str:
        total = sum(int(d) * w for d, w in zip(base, range(weight_start, 1, -1)))
        rem = total % 11
        return str(0 if rem < 2 else 11 - rem)

    d1 = calc(digits[:9], 10)
    d2 = calc(digits[:9] + d1, 11)
    return digits[-2:] == d1 + d2


def exact_cpf_match(cpf: str, text: str) -> bool:
    target = re.sub(r"\D", "", cpf)
    if len(target) != 11 or not text:
        return False
    for match in CPF_CANDIDATE_RE.finditer(text):
        if re.sub(r"\D", "", match.group(1)) == target:
            return True
    return False


def criteria_match(criteria: SearchCriteria, text: str) -> tuple[bool, bool, bool]:
    name_ok = True if not criteria.name else exact_name_match(criteria.name, text)
    cpf_ok = True if not criteria.cpf else exact_cpf_match(criteria.cpf, text)
    return name_ok, cpf_ok, name_ok and cpf_ok


def redact_target_cpf(text: str, cpf: str | None) -> str:
    if not text or not cpf:
        return text
    masked = mask_cpf(cpf) or "***"
    target = re.sub(r"\D", "", cpf)
    return CPF_CANDIDATE_RE.sub(
        lambda m: masked if re.sub(r"\D", "", m.group(1)) == target else m.group(0),
        text,
    )


def canonical_url(url: str) -> str:
    try:
        p = urlparse(url)
        if not p.netloc:
            return url
        path = re.sub(r"/+", "/", p.path or "/")
        if path != "/":
            path = path.rstrip("/")
        return urlunparse((p.scheme.lower() or "https", p.netloc.lower(), path, "", p.query, ""))
    except Exception:
        return url


def clean_html_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    if "<" in text and ">" in text:
        text = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def deep_strings(obj: Any) -> Iterable[str]:
    if isinstance(obj, dict):
        for value in obj.values():
            yield from deep_strings(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from deep_strings(value)
    elif obj is not None:
        yield clean_html_text(obj)


def get_path(obj: Any, path: str | None) -> Any:
    if not path or path == "$":
        return obj
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def string_field(obj: dict[str, Any], path: str) -> str:
    value = get_path(obj, path)
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return " ".join(deep_strings(value))
    return clean_html_text(value)


def _flatten_context(prefix: str, value: Any, out: dict[str, str], depth: int = 3) -> None:
    """Achata dicts aninhados em chaves pontilhadas (ex.: repo.raw) para templates."""
    if depth <= 0:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            full = f"{prefix}.{key}" if prefix else key
            if isinstance(child, dict):
                _flatten_context(full, child, out, depth - 1)
            elif isinstance(child, list):
                if child and all(isinstance(x, (str, int, float)) for x in child[:8]):
                    out[full] = " ".join(str(x) for x in child)
            elif isinstance(child, str):
                out[full] = child
                if len(child) <= 500:
                    out[f"{full}_url"] = quote(child, safe="")
                    out[f"{full}_slug"] = quote(child.replace(" ", "_"), safe="_-")
            elif isinstance(child, (int, float)):
                out[full] = str(child)
            elif child is None:
                out[full] = ""


def template_context(criteria: SearchCriteria, extra: dict[str, Any] | None = None) -> dict[str, str]:
    context: dict[str, str] = {
        "name": criteria.name or "",
        "cpf": criteria.cpf or "",
        "cpf_formatted": criteria.cpf_formatted or "",
    }
    if extra:
        for key, value in extra.items():
            if isinstance(value, (str, int, float)) or value is None:
                raw = "" if value is None else str(value)
                context[key] = raw
                context[f"{key}_url"] = quote(raw, safe="")
                context[f"{key}_slug"] = quote(raw.replace(" ", "_"), safe="_-")
            elif isinstance(value, dict):
                _flatten_context(key, value, context)
    context["name_url"] = quote(context["name"], safe="")
    context["name_slug"] = quote(context["name"].replace(" ", "_"), safe="_-")
    return context


class SafeFormat(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_template(value: Any, context: dict[str, str]) -> Any:
    if isinstance(value, str):
        def env_replace(match: re.Match[str]) -> str:
            return os.getenv(match.group(1), "")
        out = ENV_RE.sub(env_replace, value)
        return out.format_map(SafeFormat(context))
    if isinstance(value, dict):
        return {k: render_template(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [render_template(v, context) for v in value]
    return value


def clean_headers(headers: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {"User-Agent": USER_AGENT}
    for key, value in headers.items():
        v = str(value).strip()
        if not v:
            continue
        if v.lower() in {"bearer", "token", "basic"}:
            continue
        if v.lower().endswith(("bearer", "token", "basic")):
            continue
        out[str(key)] = v
    return out


def load_providers(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    providers = data.get("providers")
    if not isinstance(providers, list):
        raise ValueError("providers.json deve conter uma lista em 'providers'.")
    ids: set[str] = set()
    for p in providers:
        if not isinstance(p, dict) or not p.get("id") or not p.get("kind"):
            raise ValueError("Todo provider precisa de 'id' e 'kind'.")
        if p["id"] in ids:
            raise ValueError(f"Provider duplicado: {p['id']}")
        ids.add(p["id"])
    return providers


def provider_supports(provider: dict[str, Any], mode: str) -> bool:
    return bool(provider.get("enabled", True) and mode in provider.get("modes", []))


def match_rule(criteria: SearchCriteria, record: dict[str, Any], rule: dict[str, Any] | None) -> tuple[bool, bool, bool]:
    matched_name = True if not criteria.name else False
    matched_cpf = True if not criteria.cpf else False

    if criteria.name:
        name_rule = (rule or {}).get("name", {})
        fields = name_rule.get("fields", [])
        mode = name_rule.get("mode", "contains")
        values = [string_field(record, field) for field in fields] if fields else [" ".join(deep_strings(record))]
        if mode == "equal":
            matched_name = any(exact_name_equal(criteria.name, value) for value in values if value)
        else:
            matched_name = any(exact_name_match(criteria.name, value) for value in values if value)

    if criteria.cpf:
        cpf_rule = (rule or {}).get("cpf", {})
        fields = cpf_rule.get("fields", [])
        values = [string_field(record, field) for field in fields] if fields else [" ".join(deep_strings(record))]
        matched_cpf = any(exact_cpf_match(criteria.cpf, value) for value in values if value)

    return matched_name, matched_cpf, matched_name and matched_cpf


def compact_data(record: dict[str, Any], keep_fields: list[str] | None, cpf: str | None, max_chars: int | None = None) -> dict[str, Any]:
    if keep_fields:
        data = {field: get_path(record, field) for field in keep_fields if get_path(record, field) is not None}
    else:
        data = dict(record)

    def redact(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: redact(v) for k, v in value.items()}
        if isinstance(value, list):
            return [redact(v) for v in value[:100]]
        if isinstance(value, str):
            if max_chars and len(value) > max_chars:
                value = value[:max_chars] + " …[truncado]"
            return redact_target_cpf(value, cpf)
        return value

    return redact(data)


def ddgs_provider(provider: dict[str, Any], criteria: SearchCriteria, args: argparse.Namespace) -> ProviderResult:
    provider_id = provider["id"]
    provider_name = provider.get("name", provider_id)
    if DDGS is None:
        return ProviderResult(provider_id, provider_name, "ddgs", False, "pacote ddgs não instalado", [], ["ddgs ausente"])

    templates = provider.get("queries", {}).get(criteria.mode, [])
    context = template_context(criteria)
    queries = list(dict.fromkeys(render_template(templates, context)))
    records: list[ProviderRecord] = []
    errors: list[str] = []
    seen: set[str] = set()

    for query in queries:
        try:
            results = DDGS(timeout=args.timeout).text(
                query=query,
                region=args.region,
                safesearch="moderate",
                max_results=max(1, min(args.max_results, 20)),
                backend=provider.get("backend", "auto"),
            )
            for item in list(results or []):
                url = str(item.get("href") or item.get("url") or "").strip()
                if not url.startswith(("http://", "https://")):
                    continue
                title = clean_html_text(item.get("title"))
                snippet = clean_html_text(item.get("body") or item.get("snippet"))
                matched_name, matched_cpf, matched_all = criteria_match(criteria, f"{title} {snippet}")
                if not matched_all:
                    continue
                url = canonical_url(url)
                if url in seen:
                    continue
                seen.add(url)
                records.append(ProviderRecord(
                    provider=provider_id,
                    provider_name=provider_name,
                    title=redact_target_cpf(title, criteria.cpf),
                    url=url,
                    data={
                        "query": redact_target_cpf(query, criteria.cpf),
                        "title": redact_target_cpf(title, criteria.cpf),
                        "snippet": redact_target_cpf(snippet, criteria.cpf),
                        "url": url,
                    },
                    matched_name=matched_name,
                    matched_cpf=matched_cpf,
                ))
        except Exception as exc:
            errors.append(redact_target_cpf(f"{query}: {exc}", criteria.cpf))
        time.sleep(0.2)

    status = f"{len(records)} resultado(s) confirmado(s)"
    return ProviderResult(provider_id, provider_name, "ddgs", True, status, records, errors)


def json_api_provider(provider: dict[str, Any], criteria: SearchCriteria, args: argparse.Namespace) -> ProviderResult:
    provider_id = provider["id"]
    provider_name = provider.get("name", provider_id)
    context = template_context(criteria)
    url = render_template(provider["url"], context)
    headers = clean_headers(render_template(provider.get("headers", {}), context))
    records: list[ProviderRecord] = []
    errors: list[str] = []

    # params_by_mode permite várias consultas por modo (ex.: grep.app), como as queries do ddgs
    param_sets = provider.get("params_by_mode", {}).get(criteria.mode)
    if param_sets is None:
        param_sets = [provider.get("params", {})]
    if not isinstance(param_sets, list):
        param_sets = [param_sets]

    seen_params: set[str] = set()
    for raw_params in param_sets:
        if not isinstance(raw_params, dict):
            continue
        params = render_template(raw_params, context)
        key = json.dumps(params, sort_keys=True, ensure_ascii=False)
        if key in seen_params:
            continue
        seen_params.add(key)

        try:
            r = requests.get(url, params=params, headers=headers, timeout=args.timeout)
            if r.status_code >= 400:
                errors.append(redact_target_cpf(f"HTTP {r.status_code} ({json.dumps(params, ensure_ascii=False)})", criteria.cpf))
                continue
            payload = r.json()
        except Exception as exc:
            errors.append(redact_target_cpf(str(exc), criteria.cpf))
            continue

        items = get_path(payload, provider.get("items_path", "$"))
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            continue

        for raw_item in items[: max(args.max_api_items, 1)]:
            if not isinstance(raw_item, dict):
                continue
            item = dict(raw_item)

            detail = provider.get("detail")
            if detail:
                detail_context = template_context(criteria, item)
                detail_url = render_template(detail.get("url", ""), detail_context)
                if detail_url:
                    detail_headers = clean_headers(render_template(detail.get("headers", provider.get("headers", {})), detail_context))
                    try:
                        dr = requests.get(detail_url, headers=detail_headers, timeout=args.timeout)
                        if dr.status_code == 200:
                            detail_data = dr.json()
                            if isinstance(detail_data, dict):
                                if detail.get("merge", True):
                                    merged = dict(item)
                                    merged.update(detail_data)
                                    item = merged
                                else:
                                    item = detail_data
                    except Exception as exc:
                        errors.append(f"detail {provider_name}: {exc}")

            matched_name, matched_cpf, matched_all = match_rule(criteria, item, provider.get("match"))
            if not matched_all:
                continue

            title = string_field(item, provider.get("title_field", "")) or provider_name
            record_context = template_context(criteria, item)
            url_value: str | None = None
            if provider.get("url_field"):
                candidate = string_field(item, provider["url_field"])
                if candidate.startswith(("http://", "https://")):
                    url_value = canonical_url(candidate)
            if not url_value and provider.get("url_template"):
                candidate = render_template(provider["url_template"], record_context)
                if candidate.startswith(("http://", "https://")):
                    url_value = canonical_url(candidate)

            records.append(ProviderRecord(
                provider=provider_id,
                provider_name=provider_name,
                title=redact_target_cpf(title, criteria.cpf),
                url=url_value,
                data=compact_data(item, provider.get("keep_fields"), criteria.cpf, provider.get("max_field_chars")),
                matched_name=matched_name,
                matched_cpf=matched_cpf,
            ))

    return ProviderResult(
        provider_id, provider_name, "json_api", True,
        f"{len(records)} resultado(s) confirmado(s)", records, errors
    )


def execute_provider(provider: dict[str, Any], criteria: SearchCriteria, args: argparse.Namespace) -> ProviderResult:
    kind = provider.get("kind")
    if kind == "ddgs":
        return ddgs_provider(provider, criteria, args)
    if kind == "json_api":
        return json_api_provider(provider, criteria, args)
    return ProviderResult(
        provider.get("id", "?"), provider.get("name", provider.get("id", "?")),
        str(kind), False, "kind não suportado", [], [f"kind={kind}"]
    )


def clean_email(email: str) -> str | None:
    email = email.strip().strip(".,;:()[]{}<>\"'")
    if len(email) > 254 or ".." in email:
        return None
    host = email.rsplit("@", 1)[-1].lower()
    if host.endswith(("example.com", "example.org", "sentry.io", "wixpress.com")):
        return None
    return email


def fetch_page(url: str, criteria: SearchCriteria, timeout: int) -> PageInfo:
    if url.lower().split("?", 1)[0].endswith(".pdf"):
        return PageInfo(url, None, None, [], [], [], False, False, False, "PDF não baixado")
    try:
        with requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9,*/*;q=0.2"},
            timeout=timeout,
            allow_redirects=True,
            stream=True,
        ) as r:
            r.raise_for_status()
            ctype = (r.headers.get("content-type") or "").lower()
            if not any(x in ctype for x in ("text/html", "application/xhtml+xml", "text/plain")):
                return PageInfo(url, None, None, [], [], [], False, False, False, f"tipo ignorado: {ctype}")
            chunks: list[bytes] = []
            total = 0
            for chunk in r.iter_content(64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > 2_000_000:
                    break
                chunks.append(chunk)
            raw = b"".join(chunks)
            page = raw.decode(r.encoding or "utf-8", errors="replace")

        soup = BeautifulSoup(page, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        title = soup.title.get_text(" ", strip=True) if soup.title else None
        desc_node = soup.find("meta", attrs={"name": re.compile("^description$", re.I)}) or soup.find("meta", attrs={"property": "og:description"})
        description = desc_node.get("content", "").strip() if desc_node else None
        text = soup.get_text(" ", strip=True)
        searchable = " ".join(x for x in (title or "", description or "", text[:250_000]) if x)
        matched_name, matched_cpf, matched_all = criteria_match(criteria, searchable)

        emails: set[str] = set()
        phones: set[str] = set()
        socials: set[str] = set()
        if matched_all:
            for found in EMAIL_RE.findall(text[:400_000]):
                email_value = clean_email(found)
                if email_value:
                    emails.add(email_value)
            for found in PHONE_RE.findall(text[:400_000]):
                digits = re.sub(r"\D", "", found)
                if 8 <= len(digits) <= 13:
                    phones.add(re.sub(r"\s+", " ", found).strip())
            for a in soup.find_all("a", href=True):
                href = str(a.get("href") or "").strip()
                if href.lower().startswith("mailto:"):
                    email_value = clean_email(href[7:].split("?", 1)[0])
                    if email_value:
                        emails.add(email_value)
                elif href.lower().startswith("tel:"):
                    digits = re.sub(r"\D", "", href[4:])
                    if 8 <= len(digits) <= 13:
                        phones.add(href[4:].strip())
                elif href.startswith(("http://", "https://")):
                    host = urlparse(href).netloc.lower().removeprefix("www.")
                    if host in SOCIAL_HOSTS:
                        socials.add(canonical_url(href))

        return PageInfo(
            url=canonical_url(url),
            title=redact_target_cpf(title or "", criteria.cpf) or None,
            description=redact_target_cpf(description or "", criteria.cpf) or None,
            emails=sorted(emails),
            phones=sorted(phones),
            social_links=sorted(socials),
            matched_name=matched_name,
            matched_cpf=matched_cpf,
            matched_all=matched_all,
        )
    except Exception as exc:
        return PageInfo(url, None, None, [], [], [], False, False, False, str(exc))


def provider_record_public_values(record: ProviderRecord) -> tuple[set[str], set[str], set[str]]:
    emails: set[str] = set()
    phones: set[str] = set()
    urls: set[str] = set()
    for value in deep_strings(record.data):
        for found in EMAIL_RE.findall(value):
            email_value = clean_email(found)
            if email_value:
                emails.add(email_value)
        for found in PHONE_RE.findall(value):
            digits = re.sub(r"\D", "", found)
            if 8 <= len(digits) <= 13:
                phones.add(found.strip())
        for found in URL_RE.findall(value):
            urls.add(found.rstrip(".,);]"))
    if record.url:
        urls.add(record.url)
    return emails, phones, urls


def collect_summary(provider_results: list[ProviderResult], pages: list[PageInfo]) -> dict[str, Any]:
    email_sources: dict[str, set[str]] = {}
    phone_sources: dict[str, set[str]] = {}
    profile_urls: set[str] = set()

    for result in provider_results:
        for record in result.records:
            emails, phones, urls = provider_record_public_values(record)
            source = f"provider:{result.provider}"
            for e in emails:
                email_sources.setdefault(e, set()).add(source)
            for p in phones:
                phone_sources.setdefault(p, set()).add(source)
            for u in urls:
                host = urlparse(u).netloc.lower().removeprefix("www.")
                if host in SOCIAL_HOSTS or host.endswith("wikipedia.org") or host.endswith("wikidata.org"):
                    profile_urls.add(u)

    for page in pages:
        if not page.matched_all:
            continue
        for e in page.emails:
            email_sources.setdefault(e, set()).add(page.url)
        for p in page.phones:
            phone_sources.setdefault(p, set()).add(page.url)
        profile_urls.update(page.social_links)

    return {
        "emails_publicados": [
            {"valor": value, "fontes": sorted(sources)}
            for value, sources in sorted(email_sources.items())
        ],
        "telefones_publicados": [
            {"valor": value, "fontes": sorted(sources)}
            for value, sources in sorted(phone_sources.items())
        ],
        "perfis_e_paginas": sorted(profile_urls),
        "total_fontes_com_resultado": sum(1 for r in provider_results if r.records),
        "total_registros_confirmados": sum(len(r.records) for r in provider_results),
    }


# ---------------------------------------------------------------------------
# Fase de verificação de vazamentos (dados vazados)
# Fontes: HIBP (chave gratuita via HIBP_API_KEY), Proxynova Comb, EmailRep.
# O identificador usado por bases vazadas é o e-mail; os e-mails são coletados
# dos registros/páginas confirmados nas buscas (ou informados via --emails).
# ---------------------------------------------------------------------------

def _record_data_emails(data: dict[str, Any]) -> set[str]:
    emails: set[str] = set()
    for value in deep_strings(data):
        for found in EMAIL_RE.findall(value):
            email_value = clean_email(found)
            if email_value:
                emails.add(email_value)
    return emails


def collect_emails_from_searches(searches: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """E-mails confirmados nas buscas (registros + páginas), com a fonte de cada um."""
    email_sources: dict[str, set[str]] = {}
    for label, search in searches.items():
        for source in search.get("fontes", []):
            provider_id = source.get("provider") or label
            for record in source.get("registros", []):
                if not isinstance(record, dict):
                    continue
                for email_value in _record_data_emails(record.get("data", {})):
                    email_sources.setdefault(email_value, set()).add(f"provider:{provider_id}")
        for page in search.get("paginas_analisadas", []):
            if not isinstance(page, dict) or not page.get("matched_all"):
                continue
            for email_value in page.get("emails", []) or []:
                if isinstance(email_value, str) and EMAIL_RE.fullmatch(email_value):
                    email_sources.setdefault(email_value.lower(), set()).add(page.get("url", "?"))
    return {email_value: sorted(sources) for email_value, sources in sorted(email_sources.items())}


def merge_extra_emails(email_sources: dict[str, list[str]], raw: str | None) -> None:
    """Adiciona e-mails passados via --emails (fonte 'cli')."""
    for token in re.split(r"[\s,;]+", raw or ""):
        token = token.strip().lower()
        if not EMAIL_RE.fullmatch(token):
            continue
        if token in email_sources:
            if "cli" not in email_sources[token]:
                email_sources[token].append("cli")
        else:
            email_sources[token] = ["cli"]


def _retry_seconds(response: Any) -> float:
    try:
        return max(1.0, float(response.headers.get("retry-after") or 2))
    except (TypeError, ValueError):
        return 2.0


def _breach_compact(breach: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in (
        "Name", "Title", "Domain", "BreachDate", "AddedDate", "ModifiedDate",
        "PwnCount", "DataClasses", "IsVerified", "IsFabricated", "IsSensitive",
        "IsRetired", "IsSpamList",
    ):
        if key in breach:
            out[key] = breach[key]
    description = clean_html_text(breach.get("Description", ""))
    if description:
        out["Description"] = description[:400] + ("…" if len(description) > 400 else "")
    return out


def check_hibp(email: str, key: str, timeout: int) -> dict[str, Any]:
    url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{quote(email, safe='')}"
    headers = {"hibp-api-key": key, "User-Agent": USER_AGENT}
    params = {"truncateResponse": "false", "includeUnverified": "true"}
    for attempt in range(2):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
        except Exception as exc:
            return {"ok": False, "erros": [str(exc)], "vazamentos": []}
        if response.status_code == 200:
            try:
                breaches = response.json()
            except Exception as exc:
                return {"ok": False, "erros": [f"JSON inválido: {exc}"], "vazamentos": []}
            return {
                "ok": True,
                "erros": [],
                "vazamentos": [_breach_compact(item) for item in breaches if isinstance(item, dict)],
            }
        if response.status_code == 404:
            return {"ok": True, "erros": [], "vazamentos": []}
        if response.status_code == 429 and attempt == 0:
            time.sleep(_retry_seconds(response))
            continue
        if response.status_code == 401:
            return {"ok": False, "erros": ["chave HIBP inválida (HTTP 401)"], "vazamentos": []}
        return {"ok": False, "erros": [f"HTTP {response.status_code}"], "vazamentos": []}
    return {"ok": False, "erros": ["rate limit HIBP persistente"], "vazamentos": []}


def check_proxynova(email: str, timeout: int) -> dict[str, Any]:
    try:
        response = requests.get(
            "https://api.proxynova.com/comb",
            params={"query": email},
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
        )
    except Exception as exc:
        return {"ok": False, "erros": [str(exc)], "total": 0, "linhas": []}
    if response.status_code != 200:
        return {"ok": False, "erros": [f"HTTP {response.status_code}"], "total": 0, "linhas": []}
    try:
        payload = response.json()
    except Exception as exc:
        return {"ok": False, "erros": [f"JSON inválido: {exc}"], "total": 0, "linhas": []}
    lines = payload.get("lines") or []
    if not isinstance(lines, list):
        lines = []
    try:
        total = int(payload.get("count") or len(lines))
    except (TypeError, ValueError):
        total = len(lines)
    return {
        "ok": True,
        "erros": [],
        "total": total,
        "linhas": [str(line) for line in lines[:100]],
    }


def check_emailrep(email: str, timeout: int) -> dict[str, Any]:
    headers = {"User-Agent": USER_AGENT}
    key = os.getenv("EMAILREP_KEY", "").strip()
    if key:
        headers["Key"] = key
    try:
        response = requests.get(
            f"https://emailrep.io/{quote(email, safe='')}",
            headers=headers,
            timeout=timeout,
        )
    except Exception as exc:
        return {"ok": False, "erros": [str(exc)], "perfis": []}
    if response.status_code == 429:
        return {"ok": False, "erros": ["rate limit EmailRep (sem chave ~10/dia; defina EMAILREP_KEY p/ mais)"], "perfis": []}
    if response.status_code != 200:
        return {"ok": False, "erros": [f"HTTP {response.status_code}"], "perfis": []}
    try:
        payload = response.json()
    except Exception as exc:
        return {"ok": False, "erros": [f"JSON inválido: {exc}"], "perfis": []}
    details = payload.get("details") or {}
    return {
        "ok": True,
        "erros": [],
        "reputacao": payload.get("reputation"),
        "suspicious": payload.get("suspicious"),
        "credentials_leaked": details.get("credentials_leaked"),
        "credentials_leaked_recent": details.get("credentials_leaked_recent"),
        "data_breaches": details.get("data_breaches"),
        "perfis": details.get("profiles") or [],
    }


def run_leak_checks(emails: dict[str, list[str]], args: argparse.Namespace) -> dict[str, Any]:
    hibp_key = os.getenv("HIBP_API_KEY", "").strip()
    limit = max(1, min(args.max_leak_emails, len(emails)))
    section: dict[str, Any] = {
        "hibp_habilitado": bool(hibp_key),
        "emails_verificados": 0,
        "emails_ignorados_por_limite": max(0, len(emails) - limit),
        "resumo": {
            "emails_com_vazamento": 0,
            "total_vazamentos_hibp": 0,
            "total_linhas_comb": 0,
            "emails_com_credencial_vazada_emailrep": 0,
        },
        "por_email": [],
    }
    for email, sources in list(emails.items())[:limit]:
        entry: dict[str, Any] = {"email": email, "fontes_publicas": sources}

        if hibp_key:
            entry["hibp"] = check_hibp(email, hibp_key, args.timeout)
            time.sleep(1.6)  # limite gratuito da HIBP (~1 requisição/1.5s)
        else:
            entry["hibp"] = {
                "ok": False,
                "erros": ["HIBP_API_KEY não definida (chave gratuita em https://haveibeenpwned.com/API/Key)"],
                "vazamentos": [],
            }

        entry["proxynova_comb"] = check_proxynova(email, args.timeout)
        time.sleep(0.8)

        entry["emailrep"] = check_emailrep(email, args.timeout)
        time.sleep(0.8)

        section["emails_verificados"] += 1
        hibp_total = len(entry["hibp"].get("vazamentos", []))
        comb_total = int(entry["proxynova_comb"].get("total", 0) or 0)
        emailrep = entry["emailrep"]
        if hibp_total or comb_total or emailrep.get("credentials_leaked"):
            section["resumo"]["emails_com_vazamento"] += 1
        section["resumo"]["total_vazamentos_hibp"] += hibp_total
        section["resumo"]["total_linhas_comb"] += comb_total
        if emailrep.get("credentials_leaked"):
            section["resumo"]["emails_com_credencial_vazada_emailrep"] += 1
        section["por_email"].append(entry)
    return section


def build_search_modes(name: str | None, cpf: str | None) -> list[tuple[str, SearchCriteria]]:
    modes: list[tuple[str, SearchCriteria]] = []
    if name:
        modes.append(("somente_nome", SearchCriteria(name=name, cpf=None)))
    if cpf:
        modes.append(("somente_cpf", SearchCriteria(name=None, cpf=cpf)))
    if name and cpf:
        modes.append(("nome_e_cpf", SearchCriteria(name=name, cpf=cpf)))
    return modes


def execute_mode(
    label: str,
    criteria: SearchCriteria,
    providers: list[dict[str, Any]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    selected = [p for p in providers if provider_supports(p, criteria.mode)]
    if args.providers:
        wanted = {x.strip() for x in args.providers.split(",") if x.strip()}
        selected = [p for p in selected if p["id"] in wanted]

    results: list[ProviderResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as pool:
        futures = {pool.submit(execute_provider, p, criteria, args): p for p in selected}
        for future in concurrent.futures.as_completed(futures):
            provider = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                results.append(ProviderResult(
                    provider["id"], provider.get("name", provider["id"]), provider.get("kind", "?"),
                    False, "erro inesperado", [], [str(exc)]
                ))

    order = {p["id"]: i for i, p in enumerate(selected)}
    results.sort(key=lambda r: order.get(r.provider, 9999))

    urls: list[str] = []
    for result in results:
        for record in result.records:
            if record.url and record.url.startswith(("http://", "https://")) and record.url not in urls:
                urls.append(record.url)

    pages: list[PageInfo] = []
    if not args.no_fetch and urls:
        urls = urls[: max(0, args.fetch_pages)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as pool:
            fetched = list(pool.map(lambda u: fetch_page(u, criteria, args.timeout), urls))
        pages = [p for p in fetched if p.matched_all]

    summary = collect_summary(results, pages)
    return {
        "criterios": {
            "modo": criteria.mode,
            "nome": criteria.name,
            "cpf": mask_cpf(criteria.cpf),
        },
        "resumo": summary,
        "fontes": [
            {
                "provider": r.provider,
                "nome": r.provider_name,
                "tipo": r.kind,
                "ok": r.ok,
                "status": r.status,
                "erros": [redact_target_cpf(e, criteria.cpf) for e in r.errors],
                "registros": [asdict(rec) for rec in r.records],
            }
            for r in results
        ],
        "paginas_analisadas": [asdict(p) for p in pages],
    }


def run_self_test(providers_path: Path) -> int:
    test_cpf = "12345678909"
    criteria_both = SearchCriteria("Marcos Vitor Silva Vasconcelos", test_cpf)
    providers = load_providers(providers_path)

    fake_gitlab = {"name": "Marcos Vitor Silva Vasconcelos", "username": "marcos", "web_url": "https://gitlab.com/marcos"}
    fake_wrong = {"name": "Marcos Victor Silva Vasconcelos", "username": "outro"}
    rule = {"name": {"fields": ["name"], "mode": "equal"}}

    checks = [
        (exact_name_match("João da Silva", "Perfil de JOAO DA SILVA"), "nome completo exato"),
        (not exact_name_match("Marcos Vitor Silva Vasconcelos", "Marcos Victor Silva Vasconcelos"), "rejeita nome parecido"),
        (cpf_valid(test_cpf), "validação CPF"),
        (criteria_match(criteria_both, "Marcos Vitor Silva Vasconcelos 123.456.789-09")[2], "nome+CPF exige ambos"),
        (match_rule(SearchCriteria("Marcos Vitor Silva Vasconcelos", None), fake_gitlab, rule)[2], "provider aceita nome exato"),
        (not match_rule(SearchCriteria("Marcos Vitor Silva Vasconcelos", None), fake_wrong, rule)[2], "provider rejeita nome parecido"),
        (len(providers) >= 5, "providers.json carregado"),
        (all(p.get("id") and p.get("kind") for p in providers), "providers possuem id/kind"),
        ("nome+cpf" in next(p for p in providers if p["id"] == "duckduckgo")["modes"], "DuckDuckGo aceita nome+CPF"),
        ("nome" in next(p for p in providers if p["id"] == "github")["modes"], "GitHub configurado para nome"),
        (all(any(p.get("id") == gov_id for p in providers) for gov_id in ("gov_portal_transparencia", "gov_dou", "gov_tse", "gov_tcu")), "providers governamentais carregados"),
        (template_context(SearchCriteria("João da Silva", None), {"repo": {"raw": "a/b"}}).get("repo.raw") == "a/b", "template_context achata campos aninhados"),
        (_record_data_emails({"txt": "contato x@y.com e z@w.com"}) == {"x@y.com", "z@w.com"}, "extração de e-mails de registros"),
        (_breach_compact({"Name": "X", "Description": "<p>desc</p>"}).get("Description") == "desc", "compactação de vazamento HIBP"),
    ]

    ok = True
    for passed, label in checks:
        print(f"[{'OK' if passed else 'FALHA'}] {label}")
        ok = ok and passed
    return 0 if ok else 1


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Agregador OSINT configurável por providers.json (fontes públicas + verificadores gratuitos de vazamento), com filtro estrito por nome/CPF."
    )
    parser.add_argument("--nome", help="Nome completo entre aspas")
    parser.add_argument("--cpf", help="CPF completo, com ou sem pontuação")
    parser.add_argument("--providers-file", default=str(script_dir / "providers.json"), help="Arquivo de providers")
    parser.add_argument("--providers", help="IDs específicos separados por vírgula")
    parser.add_argument("--list-providers", action="store_true", help="Lista providers configurados")
    parser.add_argument("--max-results", type=int, default=8, help="Resultados por consulta web (padrão: 8)")
    parser.add_argument("--max-api-items", type=int, default=50, help="Máximo de candidatos por API (padrão: 50)")
    parser.add_argument("--fetch-pages", type=int, default=20, help="URLs confirmadas a analisar (padrão: 20)")
    parser.add_argument("--workers", type=int, default=5, help="Paralelismo (padrão: 5)")
    parser.add_argument("--timeout", type=int, default=12, help="Timeout HTTP (padrão: 12s)")
    parser.add_argument("--region", default="br-pt", help="Região DDGS (padrão: br-pt)")
    parser.add_argument("--output", default="resultado.json", help="Arquivo JSON de saída")
    parser.add_argument("--no-fetch", action="store_true", help="Não baixa as páginas finais")
    parser.add_argument("--emails", help="E-mails extras para checar em bases vazadas (vírgula/espaço)")
    parser.add_argument("--max-leak-emails", type=int, default=5, help="Máx. de e-mails verificados em vazamentos (padrão: 5)")
    parser.add_argument("--no-leak-checks", action="store_true", help="Desativa a verificação de vazamentos")
    parser.add_argument("--self-test", action="store_true", help="Testes locais sem internet")
    args = parser.parse_args()

    providers_path = Path(args.providers_file).expanduser().resolve()
    try:
        providers = load_providers(providers_path)
    except Exception as exc:
        print(f"Erro carregando providers: {exc}", file=sys.stderr)
        return 2

    if args.list_providers:
        for p in providers:
            state = "ON" if p.get("enabled", True) else "OFF"
            print(f"{p['id']}: {p.get('name', p['id'])} [{state}] modos={','.join(p.get('modes', []))}")
        return 0

    if args.self_test:
        return run_self_test(providers_path)

    name = re.sub(r"\s+", " ", args.nome or "").strip() or None
    if name and (len(name) < 3 or len(name.split()) < 2):
        print("Erro: informe nome e sobrenome.", file=sys.stderr)
        return 2

    try:
        cpf = normalize_cpf(args.cpf)
    except ValueError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2

    if not name and not cpf and not (args.emails or "").strip():
        print("Erro: informe --nome, --cpf, ou --emails.", file=sys.stderr)
        return 2
    if cpf and not cpf_valid(cpf):
        print("Erro: CPF inválido (dígitos verificadores não conferem).", file=sys.stderr)
        return 2

    # --- 1) buscas públicas (arquitetura original) ---
    modes = build_search_modes(name, cpf)
    searches: dict[str, Any] = {}

    if name and cpf:
        progress = {
            "somente_nome": "[1/5] buscas por nome...",
            "somente_cpf": "[2/5] buscas por cpf...",
            "nome_e_cpf": "[3/5] buscas por nome+cpf...",
        }
        for label, criteria in modes:
            print(progress[label], flush=True)
            searches[label] = execute_mode(label, criteria, providers, args)
        print("[4/5] verificando vazamentos...", flush=True)
        final_step = "[5/5]"
    elif name or cpf:
        label, criteria = modes[0]
        print(f"[1/3] buscas por {'nome' if name else 'cpf'}...", flush=True)
        searches[label] = execute_mode(label, criteria, providers, args)
        print("[2/3] verificando vazamentos...", flush=True)
        final_step = "[3/3]"
    else:
        print("[1/2] verificando vazamentos (modo --emails)...", flush=True)
        final_step = "[2/2]"

    # --- 2) verificação de vazamentos por e-mail ---
    vazamentos: dict[str, Any] | None = None
    if not args.no_leak_checks:
        email_sources = collect_emails_from_searches(searches)
        merge_extra_emails(email_sources, args.emails)
        # normaliza caixa (ex.: Fulano@X.com == fulano@x.com)
        normalized: dict[str, list[str]] = {}
        for email, sources in email_sources.items():
            key = email.lower()
            merged = normalized.setdefault(key, [])
            for source in sources:
                if source not in merged:
                    merged.append(source)
        vazamentos = run_leak_checks(normalized, args)
        if not normalized:
            vazamentos["aviso"] = (
                "Nenhum e-mail associado encontrado nas buscas; "
                "use --emails para checar e-mails manualmente."
            )
    else:
        vazamentos = {"desativado": True}

    # --- 3) saída ---
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "consulta": {
            "nome": name,
            "cpf": mask_cpf(cpf),
            "cpf_informado": bool(cpf),
            "executado_em_utc": datetime.now(timezone.utc).isoformat(),
            "providers_file": str(providers_path),
            "escopo": "fontes públicas + verificadores gratuitos de vazamento (HIBP/Proxynova Comb/EmailRep); sem CAPTCHA/bypass e sem bases pagas",
        },
        "buscas": searches,
        "vazamentos": vazamentos,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{final_step} JSON completo: \"{output}\"", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())