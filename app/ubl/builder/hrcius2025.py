from __future__ import annotations

from decimal import Decimal

from lxml import etree

from ubl.domain.document import UblDocument
from ubl.hrcius.constants import ENDPOINT_SCHEME_ID

NSMAP = {
    None: 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2',
    'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
    'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
    'ext': 'urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2',
    'hrextac': 'urn:mfin.gov.hr:schema:xsd:HRExtensionAggregateComponents-1',
    'sac': 'urn:oasis:names:specification:ubl:schema:xsd:SignatureAggregateComponents-2',
    'sig': 'urn:oasis:names:specification:ubl:schema:xsd:CommonSignatureComponents-2',
}

CBC = '{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}'
CAC = '{urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2}'
EXT = '{urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2}'
SIG = '{urn:oasis:names:specification:ubl:schema:xsd:CommonSignatureComponents-2}'
SAC = '{urn:oasis:names:specification:ubl:schema:xsd:SignatureAggregateComponents-2}'
HREXT = '{urn:mfin.gov.hr:schema:xsd:HRExtensionAggregateComponents-1}'


def _sub(parent, tag: str, text: str | None = None, *, currency: str | None = None):
    el = etree.SubElement(parent, tag)
    if text is not None:
        el.text = str(text)
    if currency:
        el.set('currencyID', currency)
    return el


def _amount(value: Decimal, places: int = 2) -> str:
    return f'{value:.{places}f}'


def _quantity(value: Decimal, places: int = 3) -> str:
    return f'{value:.{places}f}'


def _percent(value: Decimal) -> str:
    if value == 0:
        return '0'
    return _amount(value)


def _append_hr_fisk20(extensions, document: UblDocument) -> None:
    extension = _sub(extensions, f'{EXT}UBLExtension')
    content = _sub(extension, f'{EXT}ExtensionContent')
    fisk = _sub(content, f'{HREXT}HRFISK20Data')
    hr_total = _sub(fisk, f'{HREXT}HRTaxTotal')
    vat_amount = sum(
        (sub.tax_amount for sub in document.tax_subtotals if sub.tax_scheme == 'VAT'),
        Decimal('0'),
    )
    _sub(hr_total, f'{CBC}TaxAmount', _amount(vat_amount), currency=document.currency)
    out_of_scope = Decimal('0')
    vat_exclusive = Decimal('0')
    for subtotal in document.tax_subtotals:
        sub_el = _sub(hr_total, f'{HREXT}HRTaxSubtotal')
        _sub(sub_el, f'{CBC}TaxableAmount', _amount(subtotal.taxable_amount), currency=document.currency)
        _sub(sub_el, f'{CBC}TaxAmount', _amount(subtotal.tax_amount), currency=document.currency)
        category = _sub(sub_el, f'{HREXT}HRTaxCategory')
        _sub(category, f'{CBC}ID', subtotal.category_id)
        if subtotal.name:
            _sub(category, f'{CBC}Name', subtotal.name)
        _sub(category, f'{CBC}Percent', _percent(subtotal.percent))
        if subtotal.exemption_reason:
            _sub(category, f'{CBC}TaxExemptionReason', subtotal.exemption_reason)
        scheme = _sub(category, f'{HREXT}HRTaxScheme')
        _sub(scheme, f'{CBC}ID', subtotal.tax_scheme)
        if subtotal.category_id == 'O' or subtotal.tax_scheme == 'LOC':
            out_of_scope += subtotal.taxable_amount
        else:
            vat_exclusive += subtotal.taxable_amount
    monetary = _sub(fisk, f'{HREXT}HRLegalMonetaryTotal')
    _sub(monetary, f'{CBC}TaxExclusiveAmount', _amount(vat_exclusive), currency=document.currency)
    _sub(monetary, f'{HREXT}OutOfScopeOfVATAmount', _amount(out_of_scope), currency=document.currency)


def _append_ubl_extensions(root, document: UblDocument) -> None:
    extensions = _sub(root, f'{EXT}UBLExtensions')
    _append_hr_fisk20(extensions, document)
    extension = _sub(extensions, f'{EXT}UBLExtension')
    content = _sub(extension, f'{EXT}ExtensionContent')
    doc_sigs = _sub(content, f'{SIG}UBLDocumentSignatures')
    _sub(doc_sigs, f'{SAC}SignatureInformation')


def _append_party(parent_tag: str, root, party, *, seller_contact=None) -> None:
    wrap = _sub(root, parent_tag)
    party_el = _sub(wrap, f'{CAC}Party')
    endpoint = _sub(party_el, f'{CBC}EndpointID', party.oib)
    endpoint.set('schemeID', ENDPOINT_SCHEME_ID)

    if party.party_identification:
        party_id = _sub(party_el, f'{CAC}PartyIdentification')
        _sub(party_id, f'{CBC}ID', party.party_identification)

    party_name = _sub(party_el, f'{CAC}PartyName')
    _sub(party_name, f'{CBC}Name', party.name)

    address = _sub(party_el, f'{CAC}PostalAddress')
    _sub(address, f'{CBC}StreetName', party.address.street)
    _sub(address, f'{CBC}CityName', party.address.city)
    _sub(address, f'{CBC}PostalZone', party.address.postal_zone)
    country = _sub(address, f'{CAC}Country')
    _sub(country, f'{CBC}IdentificationCode', party.address.country_code)

    tax = _sub(party_el, f'{CAC}PartyTaxScheme')
    _sub(tax, f'{CBC}CompanyID', f'HR{party.oib}')
    tax_scheme = _sub(tax, f'{CAC}TaxScheme')
    _sub(tax_scheme, f'{CBC}ID', 'VAT')

    legal = _sub(party_el, f'{CAC}PartyLegalEntity')
    _sub(legal, f'{CBC}RegistrationName', party.name)
    if party.company_legal_form:
        _sub(legal, f'{CBC}CompanyLegalForm', party.company_legal_form)

    if party.contact and (party.contact.name or party.contact.email):
        contact = _sub(party_el, f'{CAC}Contact')
        if party.contact.name:
            _sub(contact, f'{CBC}Name', party.contact.name)
        if party.contact.email:
            _sub(contact, f'{CBC}ElectronicMail', party.contact.email)

    if seller_contact is not None:
        seller = _sub(wrap, f'{CAC}SellerContact')
        _sub(seller, f'{CBC}ID', seller_contact.oib)
        _sub(seller, f'{CBC}Name', seller_contact.name)


def build_hrcius_invoice_root(document: UblDocument) -> etree._Element:
    root = etree.Element(
        '{urn:oasis:names:specification:ubl:schema:xsd:Invoice-2}Invoice',
        nsmap=NSMAP,
    )

    _append_ubl_extensions(root, document)
    _sub(root, f'{CBC}CustomizationID', document.customization_id)
    _sub(root, f'{CBC}ProfileID', document.profile_id)
    _sub(root, f'{CBC}ID', document.document_number)
    _sub(root, f'{CBC}IssueDate', document.issue_date.isoformat())
    if document.issue_time:
        _sub(root, f'{CBC}IssueTime', document.issue_time.strftime('%H:%M:%S'))
    if document.due_date:
        _sub(root, f'{CBC}DueDate', document.due_date.isoformat())
    _sub(root, f'{CBC}InvoiceTypeCode', document.invoice_type_code)
    _sub(root, f'{CBC}DocumentCurrencyCode', document.currency)

    _append_party(
        f'{CAC}AccountingSupplierParty',
        root,
        document.supplier,
        seller_contact=document.seller_contact,
    )
    _append_party(f'{CAC}AccountingCustomerParty', root, document.customer)

    if document.delivery:
        delivery = _sub(root, f'{CAC}Delivery')
        if document.delivery.actual_delivery_date:
            _sub(delivery, f'{CBC}ActualDeliveryDate', document.delivery.actual_delivery_date.isoformat())
        location = _sub(delivery, f'{CAC}DeliveryLocation')
        addr = _sub(location, f'{CAC}Address')
        country = _sub(addr, f'{CAC}Country')
        _sub(country, f'{CBC}IdentificationCode', document.delivery.country_code)

    if document.payment_means:
        payment = _sub(root, f'{CAC}PaymentMeans')
        _sub(payment, f'{CBC}PaymentMeansCode', document.payment_means.payment_means_code)
        account = _sub(payment, f'{CAC}PayeeFinancialAccount')
        _sub(account, f'{CBC}ID', document.payment_means.iban)

    if document.payment_terms_note:
        terms = _sub(root, f'{CAC}PaymentTerms')
        _sub(terms, f'{CBC}Note', document.payment_terms_note)

    currency = document.currency
    tax_total = _sub(root, f'{CAC}TaxTotal')
    total_tax = sum((sub.tax_amount for sub in document.tax_subtotals), Decimal('0'))
    _sub(tax_total, f'{CBC}TaxAmount', _amount(total_tax), currency=currency)
    for subtotal in document.tax_subtotals:
        sub_el = _sub(tax_total, f'{CAC}TaxSubtotal')
        _sub(sub_el, f'{CBC}TaxableAmount', _amount(subtotal.taxable_amount), currency=currency)
        _sub(sub_el, f'{CBC}TaxAmount', _amount(subtotal.tax_amount), currency=currency)
        category = _sub(sub_el, f'{CAC}TaxCategory')
        _sub(category, f'{CBC}ID', subtotal.category_id)
        _sub(category, f'{CBC}Percent', _percent(subtotal.percent))
        if subtotal.exemption_reason:
            _sub(category, f'{CBC}TaxExemptionReason', subtotal.exemption_reason)
        scheme = _sub(category, f'{CAC}TaxScheme')
        _sub(scheme, f'{CBC}ID', 'VAT')

    totals = document.totals
    monetary = _sub(root, f'{CAC}LegalMonetaryTotal')
    _sub(monetary, f'{CBC}LineExtensionAmount', _amount(totals.line_extension_amount), currency=currency)
    _sub(monetary, f'{CBC}TaxExclusiveAmount', _amount(totals.tax_exclusive_amount), currency=currency)
    _sub(monetary, f'{CBC}TaxInclusiveAmount', _amount(totals.tax_inclusive_amount), currency=currency)
    _sub(monetary, f'{CBC}PayableAmount', _amount(totals.payable_amount), currency=currency)

    for line in document.lines:
        line_el = _sub(root, f'{CAC}InvoiceLine')
        _sub(line_el, f'{CBC}ID', line.line_id)
        qty = _sub(line_el, f'{CBC}InvoicedQuantity', _quantity(line.quantity))
        qty.set('unitCode', line.unit_code)
        _sub(line_el, f'{CBC}LineExtensionAmount', _amount(line.line_extension_amount), currency=currency)

        item = _sub(line_el, f'{CAC}Item')
        _sub(item, f'{CBC}Name', line.item_name)
        if line.classification_code:
            classification = _sub(item, f'{CAC}CommodityClassification')
            code = _sub(classification, f'{CBC}ItemClassificationCode', line.classification_code)
            code.set('listID', line.classification_scheme)
        tax_cat = _sub(item, f'{CAC}ClassifiedTaxCategory')
        _sub(tax_cat, f'{CBC}ID', line.tax_category)
        if line.tax_name:
            _sub(tax_cat, f'{CBC}Name', line.tax_name)
        _sub(tax_cat, f'{CBC}Percent', _percent(line.tax_percent))
        if line.tax_exemption_reason:
            _sub(tax_cat, f'{CBC}TaxExemptionReason', line.tax_exemption_reason)
        scheme = _sub(tax_cat, f'{CAC}TaxScheme')
        _sub(scheme, f'{CBC}ID', 'VAT')

        price = _sub(line_el, f'{CAC}Price')
        _sub(price, f'{CBC}PriceAmount', _amount(line.unit_price, 6), currency=currency)
        base_qty = _sub(price, f'{CBC}BaseQuantity', _quantity(line.base_quantity))
        base_qty.set('unitCode', line.unit_code)

    return root
