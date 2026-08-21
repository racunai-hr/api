# racunai.hr API

Django ERP API.

Feature work lands on `develop` (WSL). Production is `main` on dedicated-hel1.

## OpenAPI contract

Source of truth: endpoints, schema serializers, `@extend_schema`, and API tests.
`openapi.yaml` is a generated versioned artifact — never edit it by hand.

```bash
scripts/generate_openapi.sh
scripts/check_openapi.sh   # CI gate: validate + fail if artifact is stale
```

Swagger UI (when the API is running): `/api/schema/swagger-ui/`
Schema JSON/YAML: `/api/schema/`

Fiscal AS4 and intermediary `/v1` are **not** part of this contract.

