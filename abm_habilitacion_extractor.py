"""
Extractor minimo de habilitaciones desde OCR (TXT o JSON OCR RAW).

Campos extraidos:
1) razon_social
2) numero_expediente
3) fecha_emision_expediente (ISO YYYY-MM-DD)
4) antiguedad_anios
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence


EXPEDIENTE_LINE_RE = re.compile(r"(?im)^\s*Expediente\s*:\s*([^\n\r]+)")
EXPEDIENTE_ID_RE = re.compile(
    r"\bEX-\d{4}-\d{6,12}(?:\s*-\s*-?[A-Z0-9#]+)*",
    flags=re.IGNORECASE,
)

FECHA_CARAT_RE = re.compile(r"(?i)\bFecha\s+Caratul[^\n\r:]{0,30}:\s*([^\n\r]+)")
ANCHOR_DATE_RE = re.compile(
    r"(?i)(?:mendoza\s*,|car[áa]tula\s+expediente|caratula\s+expediente)"
)
DATE_DMY_RE = re.compile(r"\b([0-3]?\d/[01]?\d/\d{4})\b")
DATE_YMD_RE = re.compile(r"\b(\d{4}-[01]\d-[0-3]\d)\b")

RAZON_LINE_RE = re.compile(r"(?i)\bRaz[^\n\r:]{0,20}Social\s*:\s*([^\n\r]+)")
DESC_OR_MOTIVO_RE = re.compile(
    r"(?i)(?:\bDescripci[^\n\r:]{0,20}\b|\bMotivo\s+de\s+Solicitud[^\n\r:]{0,40}\b)\s*:\s*"
)
QUOTED_TEXT_RE = re.compile(r"[\"“”]([^\"“”]{3,140})[\"“”]")
TITULAR_RE = re.compile(
    r"(?i)\b(?:TITULAR|DENOMINAD[OA])\s+(.{4,180}?)(?=\s+(?:CUIT|DNI|DOMICILIO|PAIS|PROVINCIA|$))"
)
BEFORE_CUIT_RE = re.compile(
    r"([A-Z0-9&.,'()/\-]+(?:\s+[A-Z0-9&.,'()/\-]+){1,12})\s+CUIT\b"
)

INVALID_RAZON_VALUES = {"---", "--", "-", "N/A", "NA", "NONE"}
GENERIC_RAZON_TOKENS = {
    "HABILITACION",
    "HABILITACIONES",
    "CONSULTORIOS",
    "CARATULACION",
    "SOLICITUD",
    "EXPEDIENTE",
    "APLICACION",
    "LEY",
    "DECRETO",
    "PROVINCIA",
    "MENDOZA",
}


def _normalize_text(text: str) -> str:
    text = text.replace("\r", "\n")
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", "\n", text)
    return text.strip()


def _normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _clean_expediente(value: str) -> str:
    value = _normalize_spaces(value)
    value = re.sub(r"\s*-\s*", "-", value)
    value = value.strip(" ,.;:")
    return value.upper()


def _find_first_valid_date_iso(text: str) -> str | None:
    matches: list[tuple[int, str]] = []

    for m in DATE_DMY_RE.finditer(text):
        raw = m.group(1)
        try:
            iso = datetime.strptime(raw, "%d/%m/%Y").date().isoformat()
            matches.append((m.start(), iso))
        except ValueError:
            continue

    for m in DATE_YMD_RE.finditer(text):
        raw = m.group(1)
        try:
            iso = datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
            matches.append((m.start(), iso))
        except ValueError:
            continue

    if not matches:
        return None

    matches.sort(key=lambda x: x[0])
    return matches[0][1]


def _extract_numero_expediente(text: str) -> str | None:
    by_label = EXPEDIENTE_LINE_RE.search(text)
    if by_label:
        line_value = by_label.group(1)
        by_id = EXPEDIENTE_ID_RE.search(line_value)
        if by_id:
            return _clean_expediente(by_id.group(0))
        cleaned = _clean_expediente(line_value)
        return cleaned if cleaned else None

    by_id = EXPEDIENTE_ID_RE.search(text)
    if by_id:
        return _clean_expediente(by_id.group(0))

    return None


def _extract_fecha_emision_expediente(text: str) -> str | None:
    by_label = FECHA_CARAT_RE.search(text)
    if by_label:
        direct_date = _find_first_valid_date_iso(by_label.group(1))
        if direct_date:
            return direct_date

    for anchor in ANCHOR_DATE_RE.finditer(text):
        start = max(0, anchor.start() - 80)
        end = min(len(text), anchor.end() + 320)
        around = text[start:end]
        date_iso = _find_first_valid_date_iso(around)
        if date_iso:
            return date_iso

    return None


def _clean_razon_social(value: str) -> str:
    value = value.strip().strip("\"'.,;:()[]{}")
    value = re.sub(r"(?i)^S/\s*", "", value)
    value = _normalize_spaces(value)
    return value


def _looks_like_razon_social(value: str) -> bool:
    candidate = _clean_razon_social(value)
    if not candidate:
        return False
    if candidate.upper() in INVALID_RAZON_VALUES:
        return False

    tokens = candidate.split()
    if len(tokens) < 2 or len(tokens) > 16:
        return False
    if not any(ch.isalpha() for ch in candidate):
        return False

    generic_count = sum(1 for tok in tokens if tok.upper().strip(".,") in GENERIC_RAZON_TOKENS)
    if generic_count / len(tokens) > 0.6:
        return False

    return True


def _extract_razon_from_description_or_motivo(text: str) -> str | None:
    for m in DESC_OR_MOTIVO_RE.finditer(text):
        segment = text[m.end() : m.end() + 420]
        flat_segment = _normalize_spaces(segment.replace("\n", " "))

        for quoted in QUOTED_TEXT_RE.finditer(flat_segment):
            candidate = _clean_razon_social(quoted.group(1))
            if _looks_like_razon_social(candidate):
                return candidate

        titular_match = TITULAR_RE.search(flat_segment)
        if titular_match:
            candidate = _clean_razon_social(titular_match.group(1))
            if _looks_like_razon_social(candidate):
                return candidate

        before_cuit = BEFORE_CUIT_RE.search(flat_segment)
        if before_cuit:
            candidate = _clean_razon_social(before_cuit.group(1))
            if _looks_like_razon_social(candidate):
                return candidate

    return None


def _extract_razon_social(text: str) -> str | None:
    by_label = RAZON_LINE_RE.search(text)
    if by_label:
        candidate = _clean_razon_social(by_label.group(1))
        if _looks_like_razon_social(candidate):
            return candidate

    return _extract_razon_from_description_or_motivo(text)


def _build_result(
    razon_social: str,
    numero_expediente: str,
    fecha_emision_expediente: str,
) -> dict:
    fecha_obj = datetime.strptime(fecha_emision_expediente, "%Y-%m-%d").date()
    dias = (date.today() - fecha_obj).days
    antiguedad = round(dias / 365.25, 1)

    return {
        "razon_social": razon_social,
        "numero_expediente": numero_expediente,
        "fecha_emision_expediente": fecha_emision_expediente,
        "antiguedad_anios": antiguedad,
    }


def extract_habilitacion_min(text: str) -> dict | None:
    """
    Extrae campos minimos de habilitacion desde texto OCR.

    Devuelve None si faltan campos criticos:
    - razon_social
    - numero_expediente
    - fecha_emision_expediente
    """
    if not text or not text.strip():
        return None

    normalized = _normalize_text(text)

    numero_expediente = _extract_numero_expediente(normalized)
    fecha_emision = _extract_fecha_emision_expediente(normalized)
    razon_social = _extract_razon_social(normalized)

    if not numero_expediente or not fecha_emision or not razon_social:
        return None

    return _build_result(
        razon_social=razon_social,
        numero_expediente=numero_expediente,
        fecha_emision_expediente=fecha_emision,
    )


def _load_text_file(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def _collect_strings_recursively(data: Any, out: list[str]) -> None:
    if isinstance(data, dict):
        for value in data.values():
            _collect_strings_recursively(value, out)
    elif isinstance(data, list):
        for item in data:
            _collect_strings_recursively(item, out)
    elif isinstance(data, str):
        out.append(data)


def _load_text_from_raw_json(path: Path) -> str:
    data = json.loads(_load_text_file(path))
    chunks: list[str] = []

    paginas = data.get("paginas")
    if isinstance(paginas, list):
        for pagina in paginas:
            if not isinstance(pagina, dict):
                continue
            items = pagina.get("items")
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                txt = item.get("text")
                if isinstance(txt, str):
                    chunks.append(txt)

    if chunks:
        return "\n".join(chunks)

    fallback_chunks: list[str] = []
    _collect_strings_recursively(data, fallback_chunks)
    return "\n".join(fallback_chunks)


def extract_from_file(path: str) -> dict | None:
    """
    Extrae habilitacion minima desde un archivo OCR (.txt o JSON OCR RAW).
    Devuelve el resultado con `source_file`, o None si no hay match.
    """
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        return None

    suffix = file_path.suffix.lower()
    is_raw_json_name = file_path.name.endswith("_OCR_LLM_READY_RAW.json")

    if is_raw_json_name or suffix == ".json":
        text = _load_text_from_raw_json(file_path)
    elif suffix == ".txt":
        text = _load_text_file(file_path)
    else:
        return None

    parsed = extract_habilitacion_min(text)
    if parsed is None:
        return None

    return {
        "source_file": str(file_path),
        **parsed,
    }


def _iter_input_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]

    if not input_path.is_dir():
        return []

    found: list[Path] = []
    found.extend(input_path.rglob("*.txt"))
    found.extend(input_path.rglob("*_OCR_LLM_READY_RAW.json"))

    unique = {p.resolve() for p in found if p.is_file()}
    return sorted(unique)


def _default_input_suggestions() -> list[Path]:
    base = Path(__file__).resolve().parent
    return [
        base / "temp_proceso",
        base / "raw_ocr",
        base / "json_llm" / "00_ocr",
    ]


def _safe_input(prompt: str) -> str:
    try:
        return input(prompt)
    except (KeyboardInterrupt, EOFError):
        print("\nInterrupción detectada, saliendo del selector interactivo.")
        raise SystemExit(1)


def _prompt_menu_selection(label: str, options: Sequence[Path]) -> Path:
    print(label)
    while True:
        for idx, option in enumerate(options, start=1):
            exists_flag = " (existe)" if option.exists() else " (no existe)"
            print(f"  {idx}) {option}{exists_flag}")
        print("  m) Otra ruta (archivo o carpeta)")

        choice = _safe_input("Opción: ").strip().lower()
        if choice == "m":
            manual = Path(_safe_input("Ruta manual: ").strip())
            if manual.exists():
                return manual.resolve()
            print("La ruta manual no existe, ingresala de nuevo.")
            continue

        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(options):
                candidate = options[idx - 1]
                if candidate.exists():
                    return candidate.resolve()
                print("La ruta sugerida no existe, elegí otra opción o ingresá una ruta diferente.")
                continue

        print("Opción inválida, intentá de nuevo.")


def _prompt_output_path(default: Path) -> Path:
    print("Ruta de salida para habilitaciones (enter = valor por defecto).")
    while True:
        raw = _safe_input(f"Archivo de salida [{default}]: ").strip()
        if not raw:
            return default.resolve()
        candidate = Path(raw)
        if candidate.is_dir():
            print("La salida debe ser un archivo, no una carpeta. Reintentá.")
            continue
        return candidate.resolve()


def _interactive_menu() -> tuple[Path, Path]:
    if not sys.stdin.isatty():
        raise SystemExit("El modo interactivo requiere un terminal interactivo.")

    input_label = (
        "Seleccioná la carpeta o archivo OCR que querés procesar."
        "\nMarcá la opción con el número o elegí 'm' para escribir una ruta."
    )
    input_path = _prompt_menu_selection(input_label, _default_input_suggestions())
    output_default = Path(__file__).resolve().parent / "habilitaciones.json"
    return input_path, _prompt_output_path(output_default)


def _run_cli(input_path: Path, output_path: Path) -> int:
    files = _iter_input_files(input_path)
    results: list[dict[str, Any]] = []
    match_count = 0

    for file_path in files:
        extracted = extract_from_file(str(file_path))
        if extracted is None:
            results.append({"source_file": str(file_path), "no_match": True})
            continue

        results.append(extracted)
        match_count += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    no_match_count = len(results) - match_count
    print(
        f"Procesados: {len(results)} | matches: {match_count} | no_match: {no_match_count} | output: {output_path}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extrae campos minimos de habilitaciones desde OCR (TXT/JSON RAW)."
    )
    parser.add_argument(
        "--input",
        help="Ruta de archivo o carpeta de entrada (omitir si usás --interactive).",
    )
    parser.add_argument(
        "--output",
        help="Ruta del JSON de salida (omitir si usás --interactive).",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Lanza un selector interactivo para elegir input y output.",
    )
    args = parser.parse_args()

    if args.interactive:
        input_path, output_path = _interactive_menu()
    else:
        if not args.input or not args.output:
            parser.error("Se requieren --input y --output o usar --interactive.")
        input_path = Path(args.input).resolve()
        output_path = Path(args.output).resolve()
    return _run_cli(input_path=input_path, output_path=output_path)


if __name__ == "__main__":
    raise SystemExit(main())
