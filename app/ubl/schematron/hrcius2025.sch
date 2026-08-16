<?xml version="1.0" encoding="UTF-8"?>
<schema xmlns="http://purl.oclc.org/dsdl/schematron">
  <title>HR CIUS 2025 — minimal validation rules (vendored subset)</title>
  <ns prefix="inv" uri="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"/>
  <ns prefix="cbc" uri="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"/>
  <ns prefix="cac" uri="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"/>

  <pattern id="hr-cius-customization">
    <rule context="//inv:Invoice">
      <assert test="cbc:CustomizationID = 'urn:cen.eu:en16931:2017#compliant#urn:mfin.gov.hr:cius-2025:1.0#conformant#urn:mfin.gov.hr:ext-2025:1.0'">
        CustomizationID mora biti HR CIUS 2025 profil.
      </assert>
      <assert test="cbc:ProfileID != ''">
        ProfileID je obavezan (npr. P1).
      </assert>
      <assert test="cbc:ID != ''">
        Broj računa (cbc:ID) je obavezan.
      </assert>
      <assert test="cbc:IssueDate != ''">
        Datum izdavanja (cbc:IssueDate) je obavezan.
      </assert>
    </rule>
  </pattern>

  <pattern id="hr-cius-parties">
    <rule context="//inv:Invoice/cac:AccountingSupplierParty/cac:Party/cbc:EndpointID">
      <assert test="@schemeID = '9934'">
        EndpointID izdavatelja mora imati schemeID 9934 (OIB).
      </assert>
      <assert test="string-length(normalize-space(.)) = 11">
        OIB izdavatelja mora imati 11 znamenki.
      </assert>
    </rule>
    <rule context="//inv:Invoice/cac:AccountingCustomerParty/cac:Party/cbc:EndpointID">
      <assert test="@schemeID = '9934'">
        EndpointID primatelja mora imati schemeID 9934 (OIB).
      </assert>
      <assert test="string-length(normalize-space(.)) = 11">
        OIB primatelja mora imati 11 znamenki.
      </assert>
    </rule>
  </pattern>

  <pattern id="hr-cius-totals">
    <rule context="//inv:Invoice/cac:LegalMonetaryTotal">
      <assert test="cbc:PayableAmount != ''">
        LegalMonetaryTotal/cbc:PayableAmount je obavezan.
      </assert>
    </rule>
  </pattern>
</schema>
