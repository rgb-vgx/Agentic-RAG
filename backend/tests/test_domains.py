"""Document domain taxonomy, classification helpers and API wiring."""

from app.domain.documents.domains import (
    BUILTIN_DOMAINS,
    DomainDefinition,
    domain_options,
)
from app.infrastructure.database.models import DocumentDomainRecord, DocumentRecord
from app.ingestion.domain_classifier import (
    DomainSuggestion,
    classify_by_rules,
    parse_classifier_output,
)


def test_builtin_domains_cover_required_taxonomy():
    assert set(BUILTIN_DOMAINS) == {
        "legal",
        "contract",
        "scientific",
        "financial",
        "technical",
        "medical",
        "business",
        "general",
    }
    for slug, domain in BUILTIN_DOMAINS.items():
        assert isinstance(domain, DomainDefinition)
        assert domain.slug == slug
        assert domain.label
        assert domain.is_builtin is True


def test_domain_options_merge_custom_without_shadowing_builtin():
    custom = DomainDefinition(
        slug="hr-policy",
        label="HR policy",
        description="Internal HR documents",
        is_builtin=False,
        tenant_id="t1",
    )
    shadow = DomainDefinition(
        slug="legal",
        label="Shadow",
        is_builtin=False,
        tenant_id="t1",
    )
    options = domain_options([custom, shadow])
    slugs = [item.slug for item in options]
    assert slugs.count("legal") == 1
    assert "hr-policy" in slugs
    assert len(options) == len(BUILTIN_DOMAINS) + 1


def test_rule_classifier_recognises_contract_and_financial():
    contract = classify_by_rules(
        "hop-dong-thue.pdf",
        "HỢP ĐỒNG THUÊ VĂN PHÒNG\nBên A và Bên B thỏa thuận...",
    )
    assert contract.domain == "contract"
    assert contract.source == "auto_rule"

    financial = classify_by_rules(
        "bao-cao-tai-chinh-2025.pdf",
        "BÁO CÁO TÀI CHÍNH\nBảng cân đối kế toán\nDoanh thu thuần...",
    )
    assert financial.domain == "financial"


def test_unknown_document_falls_back_to_general():
    suggestion = classify_by_rules("notes.txt", "hello world")
    assert suggestion.domain == "general"
    assert suggestion.source == "fallback"


def test_classifier_output_is_validated_against_allowed_domains():
    allowed = set(BUILTIN_DOMAINS) | {"custom-a"}
    parsed = parse_classifier_output(
        '{"domain":"custom-a","confidence":0.87,"reason":"matches hints"}',
        allowed,
    )
    assert parsed.domain == "custom-a"
    assert parsed.confidence == 0.87
    assert parsed.source == "auto_llm"

    invalid = parse_classifier_output(
        '{"domain":"not-allowed","confidence":0.99}', allowed
    )
    assert invalid.domain == "general"
    assert invalid.source == "fallback"


def test_document_persistence_has_domain_fields():
    assert hasattr(DocumentRecord, "domain")
    assert hasattr(DocumentRecord, "domain_source")
    assert hasattr(DocumentRecord, "domain_confidence")
    assert hasattr(DocumentDomainRecord, "__tablename__")
    assert DocumentDomainRecord.__tablename__ == "document_domains"


def test_domain_api_routes_are_registered():
    from app.main import app

    paths = {(route.path, tuple(sorted(route.methods or []))) for route in app.routes}
    assert ("/api/v1/domains", ("GET",)) in paths
    assert ("/api/v1/domains", ("POST",)) in paths
    assert any(path == "/api/v1/documents/classify" for path, _ in paths)
    assert any(path == "/api/v1/domains/{slug}" for path, _ in paths)


def test_domain_suggestion_shape():
    suggestion = DomainSuggestion(domain="legal", confidence=0.8, source="auto_rule")
    assert suggestion.domain == "legal"
    assert suggestion.confidence == 0.8
