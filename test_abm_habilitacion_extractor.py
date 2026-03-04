import json
from datetime import date, datetime

from abm_habilitacion_extractor import extract_from_file, extract_habilitacion_min


def _expected_antiguedad(iso_date: str) -> float:
    dt = datetime.strptime(iso_date, "%Y-%m-%d").date()
    return round((date.today() - dt).days / 365.25, 1)


def test_caratula_completa_desde_json_raw(tmp_path):
    ocr_raw = {
        "documento": "caratula.pdf",
        "document_id": "abc123",
        "paginas": [
            {
                "pagina": 1,
                "items": [
                    {"text": "Carátula Expediente"},
                    {"text": "Expediente: EX-2025-05644120- -GDEMZA-SDFYH#MSDSYD"},
                    {"text": "Fecha Caratulación: 23/07/2025"},
                    {"text": "Razón Social: REVIT S. A. S."},
                ],
            }
        ],
    }
    src = tmp_path / "sample_OCR_LLM_READY_RAW.json"
    src.write_text(json.dumps(ocr_raw, ensure_ascii=False), encoding="utf-8")

    result = extract_from_file(str(src))

    assert result is not None
    assert result["source_file"] == str(src)
    assert result["razon_social"] == "REVIT S. A. S"
    assert result["numero_expediente"] == "EX-2025-05644120--GDEMZA-SDFYH#MSDSYD"
    assert result["fecha_emision_expediente"] == "2025-07-23"
    assert result["antiguedad_anios"] == _expected_antiguedad("2025-07-23")


def test_caratula_sin_razon_social_con_fallback_descripcion():
    text = """
    Gobierno de Mendoza
    Carátula Expediente
    Expediente: EX-2024-00012345-GDEMZA-SDFYH#MSDSYD
    Fecha Caratulación: 14/02/2024
    Descripción: S/ HABILITACION CONSULTORIOS TITULAR LABORATORIO INMUNOLOGICO MENDOZA SA CUIT 30-71172034-7 DOMICILIO LEMOS 33
    """

    result = extract_habilitacion_min(text)

    assert result is not None
    assert result["razon_social"] == "LABORATORIO INMUNOLOGICO MENDOZA SA"
    assert result["numero_expediente"] == "EX-2024-00012345-GDEMZA-SDFYH#MSDSYD"
    assert result["fecha_emision_expediente"] == "2024-02-14"
    assert result["antiguedad_anios"] == _expected_antiguedad("2024-02-14")


def test_documento_sin_caratula_no_match():
    text = """
    CERTIFICADO DE COBERTURA
    Vigencia: 01/01/2026 al 01/01/2027
    Asegurado: JUAN PEREZ
    """

    assert extract_habilitacion_min(text) is None
