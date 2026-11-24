"""Output contracts of the general example bundle, with sample outputs for Mock runs.

The contracts are domain-neutral: they describe the shape of a summary, a comparison of
options and a review, not the subject matter. ``SAMPLE_OUTPUTS`` lets every workflow run in
Mock mode (no model calls, no API keys).
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from multiagent.contracts import DocumentSummary, FreeTextAnswer, StrictModel


# -- summarize_document -------------------------------------------------------------------------
class KeyPoint(StrictModel):
    id: str = Field(min_length=1)
    point: str = Field(min_length=1)
    evidence: str = Field(min_length=1)


class KeyPoints(StrictModel):
    key_points: list[KeyPoint] = Field(min_length=1)
    open_questions: list[str]


# -- research_options ---------------------------------------------------------------------------
class Finding(StrictModel):
    id: str = Field(min_length=1)
    finding: str = Field(min_length=1)
    confidence: Literal["low", "medium", "high"]
    source_note: str = Field(min_length=1)


class ResearchFindings(StrictModel):
    question: str = Field(min_length=1)
    findings: list[Finding] = Field(min_length=1)
    gaps: list[str]


class Option(StrictModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    pros: list[str] = Field(min_length=1)
    cons: list[str] = Field(min_length=1)


class OptionsComparison(StrictModel):
    criteria: list[str] = Field(min_length=1)
    options: list[Option] = Field(min_length=2)


class Recommendation(StrictModel):
    recommended_option_id: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    risks: list[str]
    next_steps: list[str] = Field(min_length=1)


# -- review_checklist ---------------------------------------------------------------------------
class ChecklistItem(StrictModel):
    criterion: str = Field(min_length=1)
    status: Literal["met", "partial", "not_met", "not_applicable"]
    comment: str = Field(min_length=1)


class ChecklistResult(StrictModel):
    items: list[ChecklistItem] = Field(min_length=1)


class ReviewReport(StrictModel):
    verdict: Literal["approved", "needs_changes", "rejected"]
    summary: str = Field(min_length=1)
    blocking_issues: list[str]
    suggestions: list[str]


class CollectedOutputs(StrictModel):
    steps: dict[str, dict[str, object]]


# -- ask_once -----------------------------------------------------------------------------------
# FreeTextAnswer and DocumentSummary live in the core (multiagent.contracts); they are re-exported here.
OUTPUT_SCHEMAS = {
    "KeyPoints": KeyPoints,
    "DocumentSummary": DocumentSummary,
    "ResearchFindings": ResearchFindings,
    "OptionsComparison": OptionsComparison,
    "Recommendation": Recommendation,
    "ChecklistResult": ChecklistResult,
    "ReviewReport": ReviewReport,
    "CollectedOutputs": CollectedOutputs,
    "FreeTextAnswer": FreeTextAnswer,
}

SAMPLE_OUTPUTS = {
    "FreeTextAnswer": {"answer": "Esta es una respuesta de ejemplo de Mock."},
    "CollectedOutputs": {"steps": {}},
    "KeyPoints": {
        "key_points": [
            {"id": "P1", "point": "La reunión semanal pasa de los lunes a los miércoles.",
             "evidence": "«A partir de marzo, la reunión semanal será los miércoles a las 10:00.»"},
            {"id": "P2", "point": "Las actas se publican en la carpeta compartida el mismo día.",
             "evidence": "«Las actas se subirán a la carpeta compartida antes de las 18:00.»"},
        ],
        "open_questions": ["No se indica qué ocurre cuando el miércoles es festivo."],
    },
    "DocumentSummary": {
        "title": "Cambio de la reunión semanal",
        "summary": "La reunión semanal se mueve a los miércoles a las 10:00 y las actas se publican el mismo día "
                   "en la carpeta compartida.",
        "key_points": [
            "Nuevo día y hora: miércoles a las 10:00.",
            "Las actas se publican antes de las 18:00 del mismo día.",
        ],
        "open_questions": ["Qué se hace si el miércoles es festivo."],
    },
    "ResearchFindings": {
        "question": "¿Conviene pasar a reuniones semanales asíncronas?",
        "findings": [
            {"id": "H1", "finding": "Las reuniones asíncronas reducen las interrupciones, pero retrasan las decisiones "
                                     "que necesitan una conversación.",
             "confidence": "medium", "source_note": "Experiencia general de equipos distribuidos; sin fuente citada."},
            {"id": "H2", "finding": "El contexto indica que el equipo trabaja en dos zonas horarias.",
             "confidence": "high", "source_note": "Dato aportado en el contexto de la consulta."},
        ],
        "gaps": ["No se conoce el coste actual, en horas, de las reuniones semanales."],
    },
    "OptionsComparison": {
        "criteria": ["Coste de tiempo", "Calidad de las decisiones", "Facilidad de adopción"],
        "options": [
            {"id": "A", "name": "Mantener la reunión síncrona",
             "description": "Una reunión semanal en directo, como ahora.",
             "pros": ["Decisiones rápidas", "No cambia nada"], "cons": ["Difícil de coordinar entre zonas horarias"]},
            {"id": "B", "name": "Pasar a un resumen asíncrono",
             "description": "Cada persona publica su avance por escrito y solo se reúnen para decidir.",
             "pros": ["Menos tiempo en reuniones", "Queda registro escrito"],
             "cons": ["Requiere disciplina para escribir", "Más lento para dudas urgentes"]},
        ],
    },
    "Recommendation": {
        "recommended_option_id": "B",
        "rationale": "Reduce el tiempo en reuniones y se adapta a las dos zonas horarias, a costa de requerir "
                     "disciplina de escritura.",
        "risks": ["Que los resúmenes se publiquen tarde o con poco detalle."],
        "next_steps": ["Probar el formato durante cuatro semanas.", "Medir el tiempo ahorrado y revisar la decisión."],
    },
    "ChecklistResult": {
        "items": [
            {"criterion": "Cada requisito tiene un responsable", "status": "partial",
             "comment": "Dos requisitos de la sección 3 no indican responsable."},
            {"criterion": "Los plazos son concretos", "status": "met",
             "comment": "Todas las entregas tienen fecha."},
            {"criterion": "Se describen los riesgos", "status": "not_met",
             "comment": "El documento no incluye una sección de riesgos."},
        ],
    },
    "ReviewReport": {
        "verdict": "needs_changes",
        "summary": "El documento es claro y tiene plazos concretos, pero le faltan responsables y riesgos.",
        "blocking_issues": ["Falta la sección de riesgos."],
        "suggestions": ["Asignar un responsable a los requisitos 3.2 y 3.4.", "Añadir una tabla de riesgos con su mitigación."],
    },
}
