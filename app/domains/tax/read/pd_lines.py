"""Retci tijela ObrazacPD v3-0 i predano tijelo Fine Star 2014.

Iznosi su bez potpisa i certifikata. Ključ je broj retka obrasca
(`34.1` odgovara elementu `Podatak341`).
"""

from __future__ import annotations

PD_LINES: tuple[tuple[str, str], ...] = (
    ('1', 'Ukupni prihodi'),
    ('2', 'Ukupni rashodi'),
    ('3', 'Dobit (1 − 2)'),
    ('4', 'Gubitak (2 − 1)'),
    ('5', 'Amortizacija'),
    ('6', '70% troškovi reprezentacije'),
    ('7', '30% troškovi za osobni prijevoz'),
    ('8', 'Manjkovi'),
    ('9', 'Rashodi od otpisa potraživanja'),
    ('10', 'Porezi i drugi doprinosi'),
    ('11', 'Kazne'),
    ('12', 'Skrivena isplata dobiti'),
    ('13', 'Darovanja i ostale pogodnosti'),
    ('14', 'Rashodi donacija iznad propisanog'),
    ('15', 'Kamate na zajmove članova'),
    ('16', 'Kamate iznad povezanih osoba'),
    ('17', 'Rashodi od nerealiziranih dobitaka'),
    ('18', 'Amortizacija iznad propisanih stopa'),
    ('19', 'Povećanje zbog promjene metode'),
    ('20', 'Vrijednosna usklađenja i otpis potraživanja'),
    ('21', 'Vrijednosna usklađenja zaliha'),
    ('22', 'Vrijednosna usklađenja financijske imovine'),
    ('23', 'Rezerviranja'),
    ('24', 'Ostala povećanja rashoda'),
    ('25', 'Ostala povećanja dobiti'),
    ('26', 'Ukupno povećanje dobiti / smanjenje gubitka'),
    ('27', 'Prihodi od dividendi i udjela u dobiti'),
    ('28', 'Reinvestirana dobit'),
    ('29', 'Naplaćena otpisana potraživanja'),
    ('30', 'Nerealizirani dobici i ostali prihodi'),
    ('31', 'Smanjenje za ostale prihode'),
    ('32', 'Smanjenje zbog promjene metode'),
    ('33', 'Trošak amortizacije priznat ranije'),
    ('34', 'Državne potpore'),
    ('34.1', 'Potpora za obrazovanje i izobrazbu'),
    ('34.2', 'Potpora za istraživačko-razvojne projekte'),
    ('35', 'Ukupno smanjenje dobiti / povećanje gubitka'),
    ('36', 'Dobit nakon povećanja i smanjenja'),
    ('37', 'Preneseni porezni gubitak (osnovica)'),
    ('38', 'Porezna osnovica'),
    ('39', 'Dobit ili gubitak nakon povećanja i smanjenja'),
    ('40', 'Preneseni porezni gubitak'),
    ('41', 'Porezni gubitak za prijenos'),
    ('42', 'Porezna osnovica za obračun'),
    ('43', 'Porezna stopa'),
    ('44', 'Porezna obveza'),
    ('45', 'Olakšice i oslobođenja'),
    ('46', 'Olakšice I. skupine'),
    ('47', 'Olakšice II. skupine'),
    ('48', 'Olakšice za poticane investicije i slobodne zone'),
    ('48.1', 'Olakšica za korisnike slobodne zone'),
    ('48.2', 'Olakšica za korisnike slobodne zone'),
    ('48.3', 'Olakšica za korisnike slobodne zone'),
    ('49', 'Poticaji investicija (čl. 8. i 9.)'),
    ('49.1', 'Olakšica uz umanjenu stopu za 50%'),
    ('49.2', 'Olakšica uz umanjenu stopu za 50%'),
    ('49.3', 'Olakšica uz umanjenu stopu za 75%'),
    ('49.4', 'Olakšica uz umanjenu stopu za 100%'),
    ('50', 'Poticaji investicija (čl. 27.)'),
    ('50.1', 'Olakšica uz stopu 50% ili 10%'),
    ('50.2', 'Olakšica uz stopu 65% ili 7%'),
    ('50.3', 'Olakšica uz stopu 85% ili 3%'),
    ('50.4', 'Olakšica uz stopu 100% ili 0%'),
    ('51', 'Ukupno poticaji investicija'),
    ('52', 'Ukupno olakšice, oslobođenja i poticaji'),
    ('53', 'Porezna obveza nakon olakšica'),
    ('54', 'Porez plaćen u inozemstvu'),
    ('55', 'Porezna obveza nakon uračunavanja'),
    ('56', 'Uplaćeni predujmovi'),
    ('57', 'Razlika za uplatu'),
    ('58', 'Razlika za povrat'),
    ('59', 'Predujmovi za sljedeće razdoblje'),
)

# Predano tijelo PD_36619131370_20140101-20141231 (ObrazacPD v3-0).
FILED_BODY_2014_OVERRIDES: dict[str, str] = {
    '1': '5.19',
    '2': '205.19',
    '4': '200.00',
    '39': '-200.00',
    '41': '200.00',
    '43': '20.00',
}


def filed_body_2014() -> dict[str, str]:
    body = {code: '0.00' for code, _label in PD_LINES}
    body.update(FILED_BODY_2014_OVERRIDES)
    return body


# Predano tijelo PD_36619131370_20150101-20151231 (ObrazacPD v4-0). Sve svote su nula.
FILED_BODY_2015_OVERRIDES: dict[str, str] = {
    '43': '20.00',
}


def filed_body_2015() -> dict[str, str]:
    body = {code: '0.00' for code, _label in PD_LINES}
    body.update(FILED_BODY_2015_OVERRIDES)
    return body


# Predano tijelo PD_36619131370_20160101-20161231 (ObrazacPD v4-0).
FILED_BODY_2016_OVERRIDES: dict[str, str] = {
    '2': '230.00',
    '4': '230.00',
    '39': '-230.00',
    '40': '200.00',
    '41': '430.00',
    '43': '20.00',
}


def filed_body_2016() -> dict[str, str]:
    body = {code: '0.00' for code, _label in PD_LINES}
    body.update(FILED_BODY_2016_OVERRIDES)
    return body


# Predano tijelo PD_36619131370_20170101-20171231 (ObrazacPD v5-0).
FILED_BODY_2017_OVERRIDES: dict[str, str] = {
    '2': '1129.00',
    '4': '1129.00',
    '39': '-1129.00',
    '40': '430.00',
    '41': '1559.00',
    '43': '12.00',
}


def filed_body_2017() -> dict[str, str]:
    body = {code: '0.00' for code, _label in PD_LINES}
    body.update(FILED_BODY_2017_OVERRIDES)
    return body


# Predano tijelo PD_36619131370_20180101-20181231 (ObrazacPD v5-0).
FILED_BODY_2018_OVERRIDES: dict[str, str] = {
    '2': '230.00',
    '4': '230.00',
    '39': '-230.00',
    '40': '1559.00',
    '41': '1789.00',
    '43': '12.00',
}


def filed_body_2018() -> dict[str, str]:
    body = {code: '0.00' for code, _label in PD_LINES}
    body.update(FILED_BODY_2018_OVERRIDES)
    return body


# Predano tijelo PD_36619131370_20190101-20191231 (ObrazacPD v6-0).
FILED_BODY_2019_OVERRIDES: dict[str, str] = {
    '2': '230.00',
    '4': '230.00',
    '39': '-230.00',
    '40': '1789.00',
    '41': '2019.00',
    '43': '12.00',
}


def filed_body_2019() -> dict[str, str]:
    body = {code: '0.00' for code, _label in PD_LINES}
    body.update(FILED_BODY_2019_OVERRIDES)
    return body


# Predano tijelo PD_36619131370_20200101-20201231 (ObrazacPD v6-0).
FILED_BODY_2020_OVERRIDES: dict[str, str] = {
    '2': '0.56',
    '4': '0.56',
    '39': '-0.56',
    '40': '2019.00',
    '41': '2019.56',
    '43': '12.00',
}


def filed_body_2020() -> dict[str, str]:
    body = {code: '0.00' for code, _label in PD_LINES}
    body.update(FILED_BODY_2020_OVERRIDES)
    return body


# Predano tijelo PD_36619131370_20210101-20211231 (ObrazacPD v6-0). Obrazac, ne potvrda.
FILED_BODY_2021_OVERRIDES: dict[str, str] = {
    '1': '3809.35',
    '2': '12584.46',
    '4': '8775.11',
    '39': '-8775.11',
    '40': '1590.00',
    '41': '10365.11',
    '43': '10.00',
}


def filed_body_2021() -> dict[str, str]:
    body = {code: '0.00' for code, _label in PD_LINES}
    body.update(FILED_BODY_2021_OVERRIDES)
    return body


# Predano tijelo PD_36619131370_20220101-20221231 (ObrazacPD). Kasniji obrazac, ne potvrda.
FILED_BODY_2022_OVERRIDES: dict[str, str] = {
    '1': '721.98',
    '2': '85528.73',
    '4': '84806.75',
    '11': '200.00',
    '26': '200.00',
    '39': '-84606.75',
    '40': '8775.11',
    '41': '93381.86',
    '43': '10.00',
}


def filed_body_2022() -> dict[str, str]:
    body = {code: '0.00' for code, _label in PD_LINES}
    body.update(FILED_BODY_2022_OVERRIDES)
    return body


def filed_body(year: int) -> dict[str, str] | None:
    if year == 2014:
        return filed_body_2014()
    if year == 2015:
        return filed_body_2015()
    if year == 2016:
        return filed_body_2016()
    if year == 2017:
        return filed_body_2017()
    if year == 2018:
        return filed_body_2018()
    if year == 2019:
        return filed_body_2019()
    if year == 2020:
        return filed_body_2020()
    if year == 2021:
        return filed_body_2021()
    if year == 2022:
        return filed_body_2022()
    return None
