"""Licensing register tests.

The parsing is protected because the published file is dirty in specific ways
that would silently distort a destination-scoped count. The join is protected
because the whole point of the module is that it refuses to decide on an
address alone.
"""

import pytest

from supply_radar.locales import load_locale
from supply_radar.matching import build_idf
from supply_radar.normalise import normalise_name
from supply_radar.registry import (
    RegisterEntry,
    _split_county,
    _town,
    _tristate,
    entries_for_town,
    find_licence,
    register_name,
    street_key,
)

HR = load_locale("hr")


# Register entries are mostly boilerplate: "za turizam i usluge", "za trgovinu",
# "putnička agencija". Those words are only revealed as worthless by seeing many
# of them, which is what the real corpus of 223 entries does and what this
# stand-in has to reproduce.
_BOILERPLATE = [
    "MARANTA d.o.o. za turizam i usluge, turistička agencija",
    "HELIJADE d.o.o. za turizam i usluge, putnička agencija",
    "VESELI d.o.o. za trgovinu i usluge, turistička agencija",
    "MATER d.o.o. za turizam i usluge, turistička agencija",
    "SPALATUM d.o.o. za turizam i usluge, putnička agencija",
    "ADRIAGATE d.o.o. za trgovinu i usluge, putnička agencija",
    "BLACK TIE d.o.o. za turizam i usluge, turistička agencija",
    "VAGARI d.o.o. za turizam i usluge, putnička agencija",
]


def idf_over(*names: str):
    """The IDF table the real caller always supplies.

    Without one, name_similarity falls back to a raw token_set_ratio, and the
    legal-form padding every register entry carries dilutes a genuine match
    below the corroboration floor. Testing the no-corpus path would test
    something registry_check.py never does.
    """
    corpus = list(names) + _BOILERPLATE
    return build_idf([set(normalise_name(n, HR).split()) for n in corpus], [])


def entry(**kw) -> RegisterEntry:
    base = dict(
        legal_name="WATERWORLD j.d.o.o. za turizam i usluge, turistička agencija",
        registered_office="Ul. Tomića stine 12, 21 000 Split",
        premises="Ul. Tomića stine 12, 21 000 Split",
        town="split",
        county="splitsko-dalmatinska",
        liability_insurance=True,
        insolvency_protection=True,
        insurer="CROATIA OSIGURANJE",
        policy_expiry="1.7.2027.",
        first_registered="2018",
    )
    base.update(kw)
    return RegisterEntry(**base)


class TestParsing:
    def test_blank_protection_is_not_the_same_as_declared_no(self):
        """The ministry leaves the cell blank when it holds no declaration.
        Flattening that to False would assert something the register does not
        say, and would then be scored against the operator."""
        assert _tristate("DA") is True
        assert _tristate("da") is True
        assert _tristate("NE") is False
        assert _tristate("Ne") is False
        assert _tristate("-") is None
        assert _tristate(None) is None
        assert _tristate("") is None

    def test_county_variants_fold_to_one_value(self):
        """Two counties are genuinely misspelt at source and one has stray
        spacing. Left alone they inflate the county count from 21 to 34."""
        assert _split_county("x | Osiećko-baranjska")[1] == "osjecko-baranjska"
        assert _split_county("x | Dubrovačko - neretvanska")[1] == "dubrovacko-neretvanska"
        assert _split_county("x | Primorsko -goranska")[1] == "primorsko-goranska"
        assert _split_county("x | Splitsko-dalmatinska,")[1] == "splitsko-dalmatinska"

    def test_town_is_read_from_the_postcode_not_the_county(self):
        assert _town("Lovretska 12, 21 000 Split") == "split"
        assert _town("Buzinski prilaz 10, 10 010 Zagreb") == "zagreb"

    def test_a_premises_line_with_no_postcode_yields_no_town(self):
        assert _town("Kras 106/1 Općina Dobrinj") is None

    def test_town_filter_does_not_match_the_county_of_the_same_name(self):
        """'Splitsko-dalmatinska' contains 'Split'. A substring filter would
        count every agency in the county as trading in the city, roughly
        doubling the number."""
        in_city = entry(premises="Lovretska 12, 21 000 Split", town="split")
        in_county = entry(
            premises="Obala 3, 21 300 Makarska",
            town="makarska",
            county="splitsko-dalmatinska",
        )
        assert entries_for_town([in_city, in_county], "Split") == [in_city]


class TestStreetKey:
    def test_house_number_suffixes_do_not_break_the_key(self):
        assert street_key("Grabova 21a, 21 000 Split", HR) == street_key(
            "Grabova 21, 21000, Split, Croatia", HR
        )

    def test_generic_street_words_are_not_the_street(self):
        """Most of the Split waterfront is 'Obala' something. The distinctive
        word is what identifies the street."""
        assert street_key("Obala Lazareta 3, 21 000 Split", HR) == "lazareta|3"

    def test_an_address_with_no_number_has_no_key(self):
        assert street_key("Gat sv. Nikole, Split", HR) is None


class TestJoin:
    def test_a_shared_address_alone_never_vouches_for_an_operator(self):
        """Measured: Obala Lazareta 3 returns five different Google businesses
        and one register entry. The waterfront address is a berth, not a
        business, which is the same finding as the shared phone and the shared
        wixsite host."""
        agency = entry(
            legal_name="DAY TRIPS d.o.o. za turizam i prijevoz, turistička agencija",
            premises="Obala Lazareta 3, 21 000 Split",
        )
        assert (
            find_licence(
                "Split After Dark", "Obala Lazareta 3, 21000, Split, Croatia",
                [agency], HR,
            )
            is None
        )

    def test_the_name_decides_when_it_agrees(self):
        agency = entry(
            legal_name="ADRIATIC VISION j.d.o.o. za usluge i turistička agencija",
            premises="Poljička cesta 26, 21 000 Split",
        )
        hit = find_licence(
            "Adriatic Vision", "Poljička cesta 26, 21000, Split, Croatia",
            [agency], HR,
            idf_over("Adriatic Vision", agency.legal_name, "Blue Cave Tours"),
        )
        assert hit is not None
        assert hit.entry is agency
        assert hit.address_agreement is True

    def test_a_mid_string_legal_form_is_not_a_distinctive_word(self):
        """Register names read "WATERWORLD j.d.o.o. za turizam i usluge".
        normalise_name only strips a TRAILING legal form, so "jdoo" survived in
        the middle looking distinctive and dragged this genuine pair from 0.667
        to 0.555."""
        assert register_name(
            "WATERWORLD j.d.o.o. za turizam i usluge, turistička agencija", HR
        ).split()[0] == "waterworld"
        assert "jdoo" not in register_name("WATERWORLD j.d.o.o. za usluge", HR)

    def test_a_containment_match_still_falls_short_of_the_floor(self):
        """Honest about a known limit rather than tuning around it.

        "Waterworld Holidays" against "WATERWORLD j.d.o.o." is the same business,
        but weighted Dice caps a containment match at 2/3 because one side
        carries an extra descriptive word, so it scores 0.667 and does not
        corroborate. The floor is inherited from the phone rule, not swept for
        this join. It is left alone because the conclusion does not move: across
        floors from 0.90 to 0.50 the join runs 3 to 15 operators out of 167, and
        0 of the 40 published leads either way.
        """
        agency = entry()
        hit = find_licence(
            "Waterworld Holidays", "Ul. Tomića stine 12, 21000, Split, Croatia",
            [agency], HR,
            idf_over("Waterworld Holidays", agency.legal_name),
        )
        assert hit is None

    def test_a_legal_form_alone_is_not_agreement(self):
        """Every entry ends in 'd.o.o. turistička agencija'. If the normaliser
        let those through, every operator would match every agency."""
        agency = entry(legal_name="MARANTA d.o.o. Turistička agencija")
        assert (
            find_licence("Blue Cave d.o.o. turistička agencija", None, [agency], HR)
            is None
        )

    def test_no_entries_is_not_an_error(self):
        assert find_licence("Anything", "Anywhere 1", [], HR) is None
