"""Non-regression sur des avis TED reels.

Ces payloads reprennent des avis effectivement renvoyes par l'API le
2026-09-07 (extraits d'un `veille test -s ted --lookback 3`). Ils fixent le
comportement attendu du filtrage sur des donnees de production, la ou le jeu
de fixtures synthetique ne suffisait pas: avant correction, ces 242 avis
etaient tous conserves avec un score de 1.00.
"""

import yaml

from veille_mp.config import EXAMPLE_CONFIG_PATH
from veille_mp.filtering import RelevanceFilter
from veille_mp.models import Notice

CFG = yaml.safe_load(EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"))["filtering"]


def notice(title: str, cpv: list[str], buyer: str = "", description: str = "") -> Notice:
    return Notice(source="ted", source_id="x", title=title, buyer_name=buyer,
                  description=description, cpv_codes=cpv)


# --------------------------------------------------------------------------
# Doivent etre CONSERVES: coeur de metier architecte
# --------------------------------------------------------------------------
GARDER = [
    (
        "Belgique - Services d'architecture, d'ingenierie et de planification - "
        "Mission d'auteurs de projet pour la transformation d'un immeuble "
        "industriel en une polyclinique",
        ["71240000"],
        "C.H.R. de la Citadelle",
    ),
    (
        "Allemagne - Services d'architecture - Objektplanung Freianlagen und "
        "Objektplanung Gebaude und Innenraume der Mobilitatsstationen",
        ["71200000", "71222000", "71221000"],
        "Kreisverwaltung des Eifelkreises Bitburg-Prum",
    ),
    (
        "France - Services d'architecture - Maitrise d'œuvre pour les travaux "
        "sur le systeme d'endiguement de Montauban",
        ["71200000"],
        "Grand Montauban Communaute d'Agglomeration",
    ),
    (
        "France - Services d'architecture - OPERATION DE CONSTRUCTION DE 55 LGTS "
        "ENVIRON ET D'UN ETABLISSEMENT PUBLIC D'ACTION SOCIALE",
        ["71200000"],
        "SA HLM DES CHALETS",
    ),
]

# --------------------------------------------------------------------------
# Doivent etre ECARTES: CPV peripherique sans signal architecture
# --------------------------------------------------------------------------
ECARTER = [
    (
        "Allemagne - Services de gestion de projets de construction - Stadtwerke "
        "Essen AG: EU-weites Vergabeverfahren \"Projektsteuerung\" (Los 5)",
        ["71541000", "71530000", "71000000", "71244000", "71248000", "71247000", "71300000"],
        "Stadtwerke Essen AG",
    ),
    (
        "France - Services topographiques - GEOREFERENCEMENT ET IDENTIFICATION DU "
        "PATRIMOINE ARBORE DE LA COMMUNAUTE D'AGGLOMERATION GRAND PARIS SUD",
        ["71351810", "71300000", "71335000", "71241000"],
        "CA GRAND PARIS SUD SEINE ESSONNE SENART",
    ),
    (
        "Allemagne - Services de conception technique - Planungsleistungen fur "
        "Verkehrsanlagen und kreuzende Bruckenbauwerke",
        ["71320000", "71322500", "71322300", "71313100", "71240000"],
        "Die Autobahn GmbH des Bundes",
    ),
]


def test_core_architecture_notices_are_kept():
    engine = RelevanceFilter(CFG)
    for title, cpv, buyer in GARDER:
        verdict = engine.evaluate(notice(title, cpv, buyer))
        assert verdict.keep, f"aurait du etre conserve: {title[:60]} ({verdict.reason})"


def test_peripheral_engineering_notices_are_rejected():
    engine = RelevanceFilter(CFG)
    for title, cpv, buyer in ECARTER:
        verdict = engine.evaluate(notice(title, cpv, buyer))
        assert not verdict.keep, f"aurait du etre ecarte: {title[:60]} (score {verdict.score})"


def test_scores_now_discriminate_instead_of_all_being_one():
    """Avant correction, tout avis portant un CPV de la liste valait 1.00."""
    engine = RelevanceFilter(CFG)
    scores = {}
    for title, cpv, buyer in GARDER + ECARTER:
        scores[title[:40]] = engine.evaluate(notice(title, cpv, buyer)).score
    assert len(set(scores.values())) > 1, f"scores non discriminants: {scores}"
    assert min(scores.values()) < 0.5 < max(scores.values())


def test_peripheral_cpv_is_lifted_by_a_relevant_keyword():
    """71240000 seul ne suffit pas, mais 'auteur de projet' le fait passer."""
    engine = RelevanceFilter(CFG)
    sans = engine.evaluate(notice("Marche de services divers", ["71240000"]))
    avec = engine.evaluate(notice("Mission d'auteurs de projet pour une ecole", ["71240000"]))
    assert not sans.keep and "peripherique" in sans.reason
    assert avec.keep and avec.score > sans.score


def test_ligature_oe_is_matched():
    """'maitrise d'oeuvre' doit reconnaitre l'ecriture avec la ligature."""
    engine = RelevanceFilter(CFG)
    verdict = engine.evaluate(notice("Mission de maîtrise d'œuvre complète", ["71240000"]))
    assert verdict.keep
    assert any("oeuvre" in k for k in verdict.matched_keywords)


def test_plural_forms_are_matched():
    engine = RelevanceFilter(CFG)
    verdict = engine.evaluate(notice("Selection d'auteurs de projets", ["71240000"]))
    assert verdict.keep


# --------------------------------------------------------------------------
# Deuxieme test terrain (Belgique seule, 14 avis): l'accord-cadre routier
# --------------------------------------------------------------------------
ACCORD_CADRE_ROUTIER = (
    "Belgique - Services d'etudes - WA/INV/2026/2 Raamovereenkomst diensten voor "
    "diverse infrastructuurprojecten op gewestwegen - Wegen Antwerpen",
    # 24 codes CPV, dont 71220000 (creation architecturale) noye dans la masse
    ["79311000", "79930000", "71300000", "73110000", "71311210", "75130000",
     "71320000", "71311300", "71313400", "71322000", "71322300", "71313440",
     "71322500", "71355000", "71241000", "45111250", "71242000", "71243000",
     "71244000", "71247000", "71400000", "71220000", "71000000", "72242000"],
    "Vlaamse Overheid",
)

MARCHE_CIBLE = (
    "Belgique - Services d'architecte pour les batiments - "
    "Project: Vorselaar, Lepelstraat vervangingsbouw - ontwerpteam",
    ["71221000"],
    "LeefGoed BV",
)


def test_catch_all_framework_with_many_cpv_is_rejected():
    """Un code coeur de metier noye dans 24 CPV ne suffit pas: sans mot-cle
    architecture dans le titre, l'avis est un accord-cadre d'ingenierie."""
    engine = RelevanceFilter(CFG)
    verdict = engine.evaluate(notice(*ACCORD_CADRE_ROUTIER[:2], buyer=ACCORD_CADRE_ROUTIER[2]))
    assert not verdict.keep
    assert "fourre-tout" in verdict.reason


def test_a_focused_notice_keeps_its_full_score():
    engine = RelevanceFilter(CFG)
    verdict = engine.evaluate(notice(*MARCHE_CIBLE[:2], buyer=MARCHE_CIBLE[2]))
    assert verdict.keep and verdict.score == 1.0


def test_dilution_is_lifted_by_an_architecture_keyword():
    """Un accord-cadre reste conserve s'il annonce clairement de l'architecture."""
    engine = RelevanceFilter(CFG)
    titre = "Raamovereenkomst architectuuropdracht voor diverse gebouwen"
    verdict = engine.evaluate(notice(titre, ACCORD_CADRE_ROUTIER[1], buyer="Vlaamse Overheid"))
    assert verdict.keep
