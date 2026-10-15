"""Consulta pontual do portal público do BNMP pela interface oficial.

O navegador é aberto em modo visível. O usuário resolve o CAPTCHA e confirma
no app; só então os campos de busca são preenchidos e a consulta é enviada
pelos controles normais do portal. Nenhum cookie é copiado ou persistido.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable
from urllib.parse import urlparse

BNMP_URL = "https://portalbnmp.cnj.jus.br/"
MAX_CAPTURED_JSON_BYTES = 5 * 1024 * 1024


def _page_fields(page) -> list[dict[str, Any]]:
    """Devolve metadados de campos visíveis, sem ler valores digitados."""
    return page.locator("input, textarea").evaluate_all(
        """els => els.map((el, index) => {
          const r = el.getBoundingClientRect();
          const style = getComputedStyle(el);
          const visible = r.width > 0 && r.height > 0 &&
            style.visibility !== 'hidden' && style.display !== 'none';
          const labels = Array.from(el.labels || []).map(x => x.innerText || '').join(' ');
          const parentText = el.closest('mat-form-field, .form-group, .field, label')?.innerText || '';
          return {
            index, visible, id: el.id || '', name: el.getAttribute('name') || '',
            placeholder: el.getAttribute('placeholder') || '',
            aria: el.getAttribute('aria-label') || '',
            label: labels, parent: parentText.slice(0, 180),
            type: (el.getAttribute('type') || el.tagName).toLowerCase(),
            disabled: !!el.disabled
          };
        }).filter(x => x.visible && !x.disabled && !['hidden','checkbox','radio','button','submit','reset'].includes(x.type))"""
    )


def _field_text(field: dict[str, Any]) -> str:
    return " ".join(str(field.get(k) or "") for k in ("id", "name", "placeholder", "aria", "label", "parent")).lower()


def _choose_fields(fields: list[dict[str, Any]], nome: str, cpf: str) -> tuple[list[tuple[int, str]], str]:
    usable = [f for f in fields if f.get("type") in {"text", "search", "tel", "number", "textarea"}]
    name_field = next((f for f in usable if re.search(r"\bnome\b|nome da pessoa", _field_text(f))), None)
    cpf_field = next((f for f in usable if re.search(r"\bcpf\b|documento", _field_text(f))), None)

    chosen: list[tuple[int, str]] = []
    criteria: list[str] = []
    if cpf and cpf_field:
        chosen.append((int(cpf_field["index"]), cpf))
        criteria.append("cpf")
    if nome and name_field and (not cpf_field or int(name_field["index"]) != int(cpf_field["index"])):
        chosen.append((int(name_field["index"]), nome))
        criteria.append("nome")

    # Algumas versões do portal usam um único campo genérico de pesquisa.
    if not chosen and len(usable) == 1:
        term = cpf or nome
        if term:
            chosen.append((int(usable[0]["index"]), term))
            criteria.append("cpf" if cpf else "nome")

    if not chosen:
        raise RuntimeError(
            "Não identifiquei com segurança os campos de nome/CPF do BNMP. "
            "A estrutura do portal pode ter mudado; nenhum dado foi enviado."
        )
    return chosen, "+".join(criteria)


def _extract_record_arrays(value: Any, depth: int = 0) -> list[list[dict[str, Any]]]:
    if depth > 5:
        return []
    found: list[list[dict[str, Any]]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in {"content", "items", "results", "records", "registros", "mandados", "pecas", "peças"}:
                if isinstance(child, list) and all(isinstance(item, dict) for item in child):
                    found.append(child)
            found.extend(_extract_record_arrays(child, depth + 1))
    elif isinstance(value, list):
        for child in value[:20]:
            found.extend(_extract_record_arrays(child, depth + 1))
    return found


def _visible_results(page) -> dict[str, Any]:
    return page.evaluate(
        """() => {
          const selectors = 'tr, [role="row"], article, mat-card, .mat-card, .card';
          const nodes = Array.from(document.querySelectorAll(selectors));
          const visible = nodes.filter(el => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
          });
          const records = visible.map(el => (el.innerText || '').trim())
            .filter(t => t.length >= 18 && t.length <= 5000);
          const unique = [...new Set(records)];
          const main = document.querySelector('main, [role="main"]');
          return {
            registros_visiveis: unique.slice(0, 100),
            texto_resultados: ((main || document.body).innerText || '').trim().slice(0, 30000)
          };
        }"""
    )


def _classify_result(records: list[dict[str, Any]], total: int | None, no_results_text: bool) -> tuple[str, str]:
    if records:
        return "resultado_publico_encontrado", f"{len(records)} registro(s) retornado(s) pela consulta pública do BNMP."
    if no_results_text or total == 0:
        return "sem_resultados_publicos", "O portal concluiu a consulta e informou que não encontrou registros para os critérios usados."
    return (
        "resultado_nao_extraido",
        "A busca foi enviada, mas não foi possível confirmar nem extrair o resultado exibido. Isso não significa ausência de registros.",
    )


def run_bnmp_public_search(
    nome: str,
    cpf: str,
    captcha_confirmed,
    update_status: Callable[[str, str], None],
    captcha_timeout: int = 300,
    search_timeout: int = 75,
) -> dict[str, Any]:
    """Abre o portal, aguarda o CAPTCHA humano e consulta pela UI pública."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright não está instalado. Rode: python -m pip install playwright "
            "e depois python -m playwright install chromium."
        ) from exc

    captured: list[Any] = []
    capture_errors: list[str] = []

    with sync_playwright() as playwright:
        browser = None
        try:
            try:
                browser = playwright.chromium.launch(channel="chrome", headless=False)
            except Exception:
                browser = playwright.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

            update_status("abrindo_portal", "Abrindo a consulta pública do BNMP em uma janela do navegador.")
            response = page.goto(BNMP_URL, wait_until="domcontentloaded", timeout=60000)
            if response is not None and response.status == 403:
                raise RuntimeError("O portal BNMP respondeu 403 neste navegador. A consulta não foi executada.")
            update_status(
                "aguardando_captcha",
                "Na janela do BNMP, resolva o CAPTCHA. Depois volte ao app e clique em “Continuar busca”.",
            )
            if not captcha_confirmed.wait(timeout=captcha_timeout):
                raise TimeoutError("Tempo de espera pelo CAPTCHA expirou. Inicie a consulta novamente.")

            fields = _page_fields(page)
            if not any(re.search(r"\bnome\b|nome da pessoa|\bcpf\b|documento", _field_text(field)) for field in fields):
                menu_item = page.get_by_text(re.compile(r"Pesquisar Mandados", re.I)).first
                if menu_item.count():
                    menu_item.click(timeout=10000)
                    page.wait_for_timeout(1200)
                    fields = _page_fields(page)
            selected_fields, criteria_used = _choose_fields(fields, nome, cpf)
            input_locator = page.locator("input, textarea")
            for index, value in selected_fields:
                input_locator.nth(index).fill(value, timeout=10000)

            last_response_at = [time.monotonic()]

            def capture_response(response) -> None:
                try:
                    parsed = urlparse(response.url)
                    if parsed.hostname not in {"portalbnmp.cnj.jus.br"}:
                        return
                    if response.status < 200 or response.status >= 300:
                        return
                    content_type = response.headers.get("content-type", "").lower()
                    if "json" not in content_type:
                        return
                    body = response.body()
                    if len(body) > MAX_CAPTURED_JSON_BYTES:
                        return
                    payload = response.json()
                    if _extract_record_arrays(payload):
                        captured.append(payload)
                        last_response_at[0] = time.monotonic()
                except Exception as exc:
                    capture_errors.append(type(exc).__name__)

            page.on("response", capture_response)
            captured.clear()
            search_pattern = re.compile(r"pesquisar|buscar|consultar|filtrar", re.I)
            forms = page.locator("form")
            form_search_button = forms.get_by_role("button", name=search_pattern).first
            search_button = page.get_by_role(
                "button", name=re.compile(r"^(pesquisar|buscar|consultar|filtrar)$", re.I)
            ).first
            if form_search_button.count():
                form_search_button.click(timeout=10000)
            elif search_button.count():
                search_button.click(timeout=10000)
            else:
                submit_input = page.locator('input[type="submit"], input[type="button"]')
                if submit_input.count() == 0:
                    raise RuntimeError("Não encontrei o botão de pesquisa do BNMP; nenhum resultado foi interpretado.")
                submit_input.first.click(timeout=10000)

            update_status("pesquisando", "Consulta enviada pela interface pública do BNMP; aguardando os resultados.")
            started = time.monotonic()
            while time.monotonic() - started < search_timeout:
                page.wait_for_timeout(750)
                visible = _visible_results(page)
                if captured and time.monotonic() - last_response_at[0] >= 2.25:
                    break
                if re.search(r"nenhum(a)? (registro|resultado|mandado|peça)|não foram encontrados|0 resultados", visible["texto_resultados"], re.I):
                    break

            visible = _visible_results(page)
            records: list[dict[str, Any]] = []
            total = None
            source_data = None
            if captured:
                source_data = captured[-1]
                arrays = _extract_record_arrays(source_data)
                if arrays:
                    records = arrays[-1]
                if isinstance(source_data, dict):
                    total = source_data.get("totalElements", source_data.get("total", source_data.get("totalElementsCount")))
                    try:
                        total = int(total) if total is not None else None
                    except (TypeError, ValueError):
                        total = None

            if not records and visible["registros_visiveis"]:
                records = [
                    {"texto_visivel": item}
                    for item in visible["registros_visiveis"]
                ]

            no_results_text = bool(re.search(
                r"nenhum(a)? (registro|resultado|mandado|peça)|não foram encontrados|0 resultados",
                visible["texto_resultados"], re.I
            ))
            status, message = _classify_result(records, total, no_results_text)

            return {
                "consulta": {
                    "fonte": "BNMP — consulta pública",
                    "url": page.url,
                    "criterios_utilizados": criteria_used,
                    "nome_pesquisado": nome or None,
                    "cpf_pesquisado": _mask_cpf(cpf) if cpf else None,
                },
                "status": status,
                "mensagem": message,
                "total_informado_pelo_portal": total,
                "registros": records,
                "registros_visiveis_na_pagina": visible["registros_visiveis"],
                "texto_resultados_visivel": visible["texto_resultados"],
                "observacao": "O resultado contém dados da consulta pública e não comprova, por si só, identidade, culpa ou antecedentes criminais completos.",
            }
        finally:
            if browser is not None:
                browser.close()


def _mask_cpf(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) != 11:
        return "informado"
    return f"***.***.***-{digits[-2:]}"
