from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from flask import Flask, jsonify, render_template, request, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
REPORTS_DIR = BASE_DIR / "relatorios"
MAIGRET_DIR = BASE_DIR / "maigret" / "maigret"
OSINT_SCRIPT = BASE_DIR / "osint.py"
PROVIDERS_FILE = BASE_DIR / "providers.json"

app = Flask(__name__)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

_state_lock = Lock()

_last_reports: dict[str, Path | None] = {
    "osint": None,
    "maigret": None,
}


def safe_slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return value.strip("._") or "consulta"


def run_command(
    command: list[str],
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
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
        osint_path = _last_reports["osint"]
        maigret_path = _last_reports["maigret"]

    if not osint_path or not maigret_path:
        return None

    if not osint_path.exists() or not maigret_path.exists():
        return None

    payload = {
        "gerado_em_utc": datetime.now(timezone.utc).isoformat(),
        "osint": load_json(osint_path),
        "maigret": load_json(maigret_path),
    }

    output = REPORTS_DIR / "resultado_integrado.json"

    output.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return output


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/osint")
def run_osint():
    data = request.get_json(silent=True) or {}

    nome = str(data.get("nome") or "").strip()
    cpf = str(data.get("cpf") or "").strip()

    if not nome and not cpf:
        return jsonify(
            {
                "ok": False,
                "erro": "Informe nome e/ou CPF.",
            }
        ), 400

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
        command.extend(
            [
                "--nome",
                nome,
            ]
        )

    if cpf:
        command.extend(
            [
                "--cpf",
                cpf,
            ]
        )

    result = run_command(
        command,
        BASE_DIR,
    )

    if result.returncode != 0 or not output.exists():
        error = (
            result.stderr
            or result.stdout
            or "Falha ao executar osint.py"
        ).strip()

        return jsonify(
            {
                "ok": False,
                "erro": error,
                "terminal": result.stdout,
            }
        ), 500

    with _state_lock:
        _last_reports["osint"] = output

    integrated = build_integrated_report()

    return jsonify(
        {
            "ok": True,
            "relatorio": load_json(output),
            "arquivo": output.name,
            "download": f"/relatorios/{output.name}",
            "integrado": (
                f"/relatorios/{integrated.name}"
                if integrated
                else None
            ),
            "terminal": result.stdout,
        }
    )


@app.post("/api/maigret")
def run_maigret():
    data = request.get_json(silent=True) or {}

    username = str(
        data.get("username") or ""
    ).strip()

    if not username:
        return jsonify(
            {
                "ok": False,
                "erro": "Informe um username.",
            }
        ), 400

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

    before = set(
        REPORTS_DIR.glob(
            "report_*_simple.json"
        )
    )

    result = run_command(
        command,
        MAIGRET_DIR,
    )

    after = set(
        REPORTS_DIR.glob(
            "report_*_simple.json"
        )
    )

    new_reports = sorted(
        after - before,
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )

    if result.returncode != 0 or not new_reports:
        error = (
            result.stderr
            or result.stdout
            or "Falha ao executar Maigret."
        ).strip()

        return jsonify(
            {
                "ok": False,
                "erro": error,
                "terminal": result.stdout,
            }
        ), 500

    generated = new_reports[0]

    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    output = (
        REPORTS_DIR
        / f"maigret_{user_slug}_{stamp}.json"
    )

    generated.replace(output)

    maigret_data = load_json(output)

    normalized = {
        "consulta": {
            "username": username,
            "executado_em_utc": datetime.now(
                timezone.utc
            ).isoformat(),
            "ferramenta": "Maigret",
        },
        "resumo": {
            "total_perfis_encontrados": len(
                maigret_data
            ),
        },
        "perfis": maigret_data,
    }

    output.write_text(
        json.dumps(
            normalized,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    with _state_lock:
        _last_reports["maigret"] = output

    integrated = build_integrated_report()

    return jsonify(
        {
            "ok": True,
            "relatorio": normalized,
            "arquivo": output.name,
            "download": f"/relatorios/{output.name}",
            "integrado": (
                f"/relatorios/{integrated.name}"
                if integrated
                else None
            ),
            "terminal": result.stdout,
        }
    )


@app.get("/relatorios/<path:filename>")
def download_report(filename: str):
    return send_from_directory(
        REPORTS_DIR,
        filename,
        as_attachment=True,
    )


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True,
    )