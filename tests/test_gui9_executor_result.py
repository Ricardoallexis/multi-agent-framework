# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The executor tracking card renders the submitted summary as text."""
from pathlib import Path

from tests.gui_text import ui_text


def test_tracking_card_uses_optional_result_and_text_interpolation():
    path = Path(__file__).resolve().parents[1] / "multiagent/gui/static/js/new-session.js"
    source = ui_text(path)
    card = source.split('aria-label="Seguimiento del ejecutor"', 1)[1].split("</section>`,", 1)[0]
    assert "tracking.data?.latest_result && html`" in card
    assert "Respuesta del ejecutor" in card
    assert "<p>${tracking.data.latest_result.summary}</p>" in card
    assert "unsafeHTML" not in card and "innerHTML" not in card
    assert "Último intento" in source and "displayExecutorEvidence" in card
    assert "Salida de error:" in source and "latestAttempt?.notes" in card
    assert "source && session.readable && html`" in card
