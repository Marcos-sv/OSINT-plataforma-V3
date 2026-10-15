from __future__ import annotations

import json
import re
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock, Thread

from flask import Flask, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent

REPORTS_DIR = BASE_DIR / "relatorios"

UPLOADS_DIR = BASE_DIR / "uploads"
FACECHECK_UPLOADS_DIR = (
    UPLOADS_DIR
    / "facecheck"
    / "original"
)

MAIGRET_DIR = BASE_DIR / "maigret" / "maigret"

OSINT_SCRIPT = BASE_DIR / "osint.py"

PROVIDERS_FILE = BASE_DIR / "providers.json"

app = Flask(__name__)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
FACECHECK_UPLOADS_DIR.mkdir(
    parents=True,
    exist_ok=True
)

app.config["MAX_CONTENT_LENGTH"] = (
    10 * 1024 * 1024
)

_state_lock = Lock()
_bnmp_jobs_lock = Lock()
_bnmp_jobs: dict[str, dict] = {}
_last_reports: dict[str, Path | None] = {"osint": None, "maigret": None, "bnmp": None}


def safe_slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return value.strip("._") or "consulta"


def run_command(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def build_integrated_report() -> Path | None:
    with _state_lock:
        paths = {key: value for key, value in _last_reports.items() if value and value.exists()}

    if len(paths) < 2:
        return None

    payload = {"gerado_em_utc": datetime.now(timezone.utc).isoformat()}
    payload.update({key: load_json(path) for key, path in paths.items()})
    output = REPORTS_DIR / "resultado_integrado.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def _set_bnmp_job(job_id: str, status: str, message: str, **extra) -> None:
    with _bnmp_jobs_lock:
        job = _bnmp_jobs.get(job_id)
        if job is not None:
            job.update({"status": status, "mensagem": message, **extra})


def _run_bnmp_job(
    job_id: str,
    nome: str,
    cpf: str,
    captcha_confirmed: Event,
    osint_report: Path | None = None,
) -> None:
    try:
        from bnmp_client import run_bnmp_public_search

        report = run_bnmp_public_search(
            nome=nome,
            cpf=cpf,
            captcha_confirmed=captcha_confirmed,
            update_status=lambda status, message: _set_bnmp_job(job_id, status, message),
        )
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = REPORTS_DIR / f"bnmp_{stamp}_{job_id[:6]}.json"
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        if osint_report and osint_report.exists():
            merged_report = load_json(osint_report)
            merged_report.setdefault("fontes_complementares", {})["bnmp"] = report
            osint_report.write_text(
                json.dumps(merged_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            primary_report = osint_report
        else:
            primary_report = output
            with _state_lock:
                _last_reports["bnmp"] = output

        integrated = build_integrated_report()
        _set_bnmp_job(
            job_id,
            "concluida",
            report["mensagem"],
            relatorio=load_json(primary_report),
            bnmp_relatorio=report,
            arquivo=primary_report.name,
            download=f"/relatorios/{primary_report.name}",
            bnmp_download=f"/relatorios/{output.name}",
            integrado=f"/relatorios/{integrated.name}" if integrated else None,
        )
    except Exception as exc:
        message = str(exc)
        if osint_report and osint_report.exists():
            try:
                merged_report = load_json(osint_report)
                merged_report.setdefault("fontes_complementares", {})["bnmp"] = {
                    "status": "consulta_nao_concluida",
                    "erro": message,
                }
                osint_report.write_text(
                    json.dumps(merged_report, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                _set_bnmp_job(
                    job_id,
                    "erro",
                    message,
                    relatorio=merged_report,
                    arquivo=osint_report.name,
                    download=f"/relatorios/{osint_report.name}",
                )
                return
            except Exception:
                pass
        _set_bnmp_job(job_id, "erro", message)

ALLOWED_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}


def allowed_image(filename: str) -> bool:
    extension = Path(filename).suffix.lower()

    return extension in ALLOWED_IMAGE_EXTENSIONS


@app.post("/api/facecheck/preparar")
def prepare_facecheck_image():

    if "imagem" not in request.files:

        return jsonify(
            {
                "ok": False,
                "erro": "Nenhuma imagem enviada.",
            }
        ), 400


    image = request.files["imagem"]


    if not image.filename:

        return jsonify(
            {
                "ok": False,
                "erro": "Selecione uma imagem.",
            }
        ), 400


    if not allowed_image(image.filename):

        return jsonify(
            {
                "ok": False,
                "erro": (
                    "Formato não permitido. "
                    "Use JPG, JPEG, PNG ou WEBP."
                ),
            }
        ), 400


    original_name = secure_filename(
        image.filename
    )


    extension = (
        Path(original_name)
        .suffix
        .lower()
    )


    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S_%f"
    )


    filename = (
        f"facecheck_{stamp}{extension}"
    )


    output_path = (
        FACECHECK_UPLOADS_DIR
        / filename
    )


    image.save(output_path)


    return jsonify(
        {
            "ok": True,

            "arquivo": filename,

            "etapa": "imagem_recebida",

            "proxima_etapa":
                "melhoria_de_imagem",

            "facecheck":
                "aguardando_integracao",

            "mensagem": (
                "Imagem recebida e salva. "
                "O próximo passo será enviar "
                "essa imagem ao sistema de "
                "melhoria."
            ),
        }
    )


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/osint")
def run_osint():
    data = request.get_json(silent=True) or {}
    nome = str(data.get("nome") or "").strip()
    cpf = str(data.get("cpf") or "").strip()

    if not nome and not cpf:
        return jsonify({"ok": False, "erro": "Informe nome e/ou CPF."}), 400

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = REPORTS_DIR / f"osint_{stamp}.json"
    command = [
        sys.executable,
        str(OSINT_SCRIPT),
        "--providers-file",
        str(PROVIDERS_FILE),
        "--output",
        str(output),
    ]
    if nome:
        command.extend(["--nome", nome])
    if cpf:
        command.extend(["--cpf", cpf])

    result = run_command(command, BASE_DIR)
    if result.returncode != 0 or not output.exists():
        error = (result.stderr or result.stdout or "Falha ao executar osint.py").strip()
        return jsonify({"ok": False, "erro": error, "terminal": result.stdout}), 500

    with _state_lock:
        _last_reports["osint"] = output
        # Cada nova busca OSINT começa um conjunto novo de relatórios.
        _last_reports["maigret"] = None
        _last_reports["bnmp"] = None
    integrated = build_integrated_report()

    return jsonify(
        {
            "ok": True,
            "relatorio": load_json(output),
            "arquivo": output.name,
            "download": f"/relatorios/{output.name}",
            "integrado": f"/relatorios/{integrated.name}" if integrated else None,
            "terminal": result.stdout,
        }
    )


@app.post("/api/bnmp/iniciar")
def start_bnmp_search():
    data = request.get_json(silent=True) or {}
    nome = str(data.get("nome") or "").strip()
    cpf = str(data.get("cpf") or "").strip()
    if not nome and not cpf:
        return jsonify({"ok": False, "erro": "Informe nome e/ou CPF para consultar o BNMP."}), 400

    osint_filename = str(data.get("osint_arquivo") or "").strip()
    osint_report = None
    if osint_filename:
        if Path(osint_filename).name != osint_filename or not osint_filename.startswith("osint_"):
            return jsonify({"ok": False, "erro": "Relatório OSINT inválido."}), 400
        candidate = REPORTS_DIR / osint_filename
        if not candidate.is_file():
            return jsonify({"ok": False, "erro": "Relatório OSINT não encontrado."}), 404
        osint_report = candidate

    with _bnmp_jobs_lock:
        active = next(
            (job_id for job_id, job in _bnmp_jobs.items()
             if job.get("status") not in {"concluida", "erro", "expirada"}),
            None,
        )
        if active:
            return jsonify({"ok": False, "erro": "Já existe uma consulta BNMP em andamento."}), 409

        job_id = secrets.token_urlsafe(12)
        captcha_confirmed = Event()
        _bnmp_jobs[job_id] = {
            "status": "iniciando",
            "mensagem": "Preparando o navegador do BNMP.",
            "captcha_confirmed": captcha_confirmed,
            "criada_em": datetime.now(timezone.utc).isoformat(),
        }

    Thread(
        target=_run_bnmp_job,
        args=(job_id, nome, cpf, captcha_confirmed, osint_report),
        daemon=True,
    ).start()
    return jsonify({"ok": True, "tarefa_id": job_id, "status": "iniciando"}), 202


@app.post("/api/bnmp/<job_id>/continuar")
def continue_bnmp_search(job_id: str):
    with _bnmp_jobs_lock:
        job = _bnmp_jobs.get(job_id)
        if not job:
            return jsonify({"ok": False, "erro": "Consulta BNMP não encontrada."}), 404
        if job.get("status") != "aguardando_captcha":
            return jsonify({"ok": False, "erro": "A consulta ainda não está aguardando a confirmação do CAPTCHA."}), 409
        job["status"] = "continuando"
        job["mensagem"] = "Continuando a consulta pela interface pública do BNMP."
        event = job["captcha_confirmed"]
        event.set()
    return jsonify({"ok": True, "status": "continuando"}), 202


@app.get("/api/bnmp/<job_id>")
def get_bnmp_search_status(job_id: str):
    with _bnmp_jobs_lock:
        job = _bnmp_jobs.get(job_id)
        if not job:
            return jsonify({"ok": False, "erro": "Consulta BNMP não encontrada."}), 404
        public_job = {
            key: value for key, value in job.items()
            if key != "captcha_confirmed" and not key.startswith("_")
        }
    return jsonify({"ok": True, **public_job})


@app.post("/api/maigret")
def run_maigret():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    if not username:
        return jsonify({"ok": False, "erro": "Informe um username."}), 400

    user_slug = safe_slug(username)
    command = [
        sys.executable,
        "-m",
        "maigret.maigret",
        username,
        "--json",
        "simple",
        "--folderoutput",
        str(REPORTS_DIR),
        "--no-progressbar",
    ]

    before = set(REPORTS_DIR.glob("report_*_simple.json"))
    result = run_command(command, MAIGRET_DIR)
    after = set(REPORTS_DIR.glob("report_*_simple.json"))
    new_reports = sorted(after - before, key=lambda item: item.stat().st_mtime, reverse=True)

    if result.returncode != 0 or not new_reports:
        error = (result.stderr or result.stdout or "Falha ao executar Maigret.").strip()
        return jsonify({"ok": False, "erro": error, "terminal": result.stdout}), 500

    generated = new_reports[0]
    # Renomeia a saída nativa para não ser sobrescrita em consultas futuras.
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = REPORTS_DIR / f"maigret_{user_slug}_{stamp}.json"
    generated.replace(output)

    maigret_data = load_json(output)
    normalized = {
        "consulta": {
            "username": username,
            "executado_em_utc": datetime.now(timezone.utc).isoformat(),
            "ferramenta": "Maigret",
        },
        "resumo": {
            "total_perfis_encontrados": len(maigret_data),
        },
        "perfis": maigret_data,
    }
    output.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")

    with _state_lock:
        _last_reports["maigret"] = output
    integrated = build_integrated_report()

    return jsonify(
        {
            "ok": True,
            "relatorio": normalized,
            "arquivo": output.name,
            "download": f"/relatorios/{output.name}",
            "integrado": f"/relatorios/{integrated.name}" if integrated else None,
            "terminal": result.stdout,
        }
    )


@app.get("/relatorios/<path:filename>")
def download_report(filename: str):
    return send_from_directory(REPORTS_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
