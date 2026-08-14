"""Normalisation is the foundation of every match decision, so it gets the
most direct test coverage in the project.

The Croatian cases here are not hypothetical. They are the exact shapes that
cause false non-matches: diacritics dropped by a CRM, legal forms present on
one side and absent on the other, and Đ transliterating to either D or DJ
depending on which system wrote the record.
"""

import pytest

from supply_radar.normalise import (
    fold_diacritics,
    normalise_name,
    normalise_phone,
    registrable_domain,
    strip_legal_suffix,
)
from supply_radar.locales import load_locale

HR = load_locale("hr")


class TestFoldDiacritics:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Šibenik", "Sibenik"),
            ("Črni Vrh", "Crni Vrh"),
            ("Ćevapi", "Cevapi"),
            ("Đakovo", "Dakovo"),
            ("Žuljana", "Zuljana"),
            ("Ludbreški", "Ludbreski"),
            ("plain ascii", "plain ascii"),
        ],
    )
    def test_folds_croatian_diacritics(self, raw, expected):
        assert fold_diacritics(raw, HR) == expected

    def test_leaves_unrelated_characters_alone(self):
        assert fold_diacritics("Tour & Travel 24/7", HR) == "Tour & Travel 24/7"


class TestStripLegalSuffix:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Adriatic Tours d.o.o.", "Adriatic Tours"),
            ("Adriatic Tours doo", "Adriatic Tours"),
            ("Adriatic Tours D.O.O.", "Adriatic Tours"),
            # The long-first ordering matters: j.d.o.o. must not be left as "j."
            ("Blue Cave j.d.o.o.", "Blue Cave"),
            ("Blue Cave jdoo", "Blue Cave"),
            ("Jadrolinija d.d.", "Jadrolinija"),
            ("Obrt Marin", "Marin"),
            ("Marin obrt", "Marin"),
            ("vl. Ivan Horvat", "Ivan Horvat"),
        ],
    )
    def test_strips_croatian_legal_forms(self, raw, expected):
        assert strip_legal_suffix(raw, HR) == expected

    def test_does_not_eat_words_that_merely_contain_a_suffix(self):
        # "Doorway" starts with "doo" but is not a legal form.
        assert strip_legal_suffix("Doorway Tours", HR) == "Doorway Tours"


class TestNormaliseName:
    def test_produces_the_same_key_for_realistic_crm_drift(self):
        google_side = "Šibenik Boat Excursions d.o.o."
        crm_side = "Sibenik Boat Excursions"
        assert normalise_name(google_side, HR) == normalise_name(crm_side, HR)

    def test_handles_dj_transliteration_of_dje(self):
        assert normalise_name("Đakovo Tours", HR) == normalise_name("Djakovo Tours", HR)

    def test_strips_generic_descriptors_that_carry_no_identity(self):
        # Both reduce to "adriatic" — generic words must not create similarity.
        assert normalise_name("Adriatic Tours", HR) == "adriatic"
        assert normalise_name("Adriatic Travel Agency", HR) == "adriatic"

    def test_is_stable_under_punctuation_and_spacing(self):
        assert normalise_name("Blue  Cave - Tours!", HR) == normalise_name(
            "Blue Cave Tours", HR
        )

    def test_returns_empty_for_a_name_that_is_only_descriptors(self):
        # Guard against a name collapsing to nothing and then matching every
        # other collapsed name. Callers must treat empty as "no name signal".
        assert normalise_name("Tours Travel Agency", HR) == ""


class TestNormalisePhone:
    @pytest.mark.parametrize(
        "raw",
        [
            "+385 21 345 678",
            "00385 21 345 678",
            "021 345 678",
            "021/345-678",
        ],
    )
    def test_croatian_numbers_converge_on_one_e164_form(self, raw):
        assert normalise_phone(raw, "HR") == "+38521345678"

    def test_returns_none_for_unparseable_input(self):
        assert normalise_phone("call us!", "HR") is None
        assert normalise_phone("", "HR") is None


class TestRegistrableDomain:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("https://www.adriatictours.hr/en/about", "adriatictours.hr"),
            ("http://adriatictours.hr", "adriatictours.hr"),
            ("ADRIATICTOURS.HR", "adriatictours.hr"),
            ("https://booking.adriatictours.hr", "adriatictours.hr"),
            # co.uk style multi-part suffixes must not be truncated wrongly.
            ("https://www.example.co.uk/tours", "example.co.uk"),
        ],
    )
    def test_reduces_urls_to_a_comparable_key(self, raw, expected):
        assert registrable_domain(raw) == expected

    def test_returns_none_when_there_is_no_usable_domain(self):
        assert registrable_domain("") is None
        assert registrable_domain("not a url") is None
