import pytest

from alchemist.report import ReportError, fill

F = {"F1": {"value": 3.63, "unit": "percentage points"}, "F2": {"value": 0, "unit": "new recipes"}}


def test_placeholders_are_filled():
    assert fill("Yield fell by {{F1}} on Line 2 from 3 to 9 August 2026.", F) == \
        "Yield fell by 3.63 percentage points on Line 2 from 3 to 9 August 2026."
    assert fill("{{F1.value}}", F) == "3.63"


@pytest.mark.parametrize("text", ["Yield fell by 3.6 points.", "Yield fell 4%.", "Temp was 2 °C lower.", "{{F9}}"])
def test_typed_numbers_and_unknown_findings_rejected(text):
    with pytest.raises(ReportError):
        fill(text, F)
