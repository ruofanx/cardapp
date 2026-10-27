"""Grade -> PriceCharting row mapping must cover every grade the UI can pick."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pricecharting_lookup as pcl


def test_special_tens_have_own_rows():
    # UI sentinel 10.5 = BGS Black Label / CGC Pristine
    assert pcl.GRADE_TO_PC_ROW[("BGS", 10.5)] == "BGS 10 Black"
    assert pcl.GRADE_TO_PC_ROW[("CGC", 10.5)] == "CGC 10 Pristine"


def test_grader_specific_tens_not_conflated():
    rows = {pcl.GRADE_TO_PC_ROW[(g, 10)] for g in ("PSA", "CGC", "BGS", "SGC")}
    assert rows == {"PSA 10", "CGC 10", "BGS 10", "SGC 10"}


def test_low_psa_grades_mapped():
    assert pcl.GRADE_TO_PC_ROW[("PSA", 6)] == "Grade 6"
    assert pcl.GRADE_TO_PC_ROW[("PSA", 5)] == "Grade 5"
