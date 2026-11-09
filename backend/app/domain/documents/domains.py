"""Document domain taxonomy.

Domains describe the broad subject area of a document. The eight built-ins
are shared by every tenant; tenants may add their own domains on top. A
custom slug can never shadow a built-in slug.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class DomainDefinition:
    slug: str
    label: str
    description: str = ""
    hints: tuple[str, ...] = ()
    is_builtin: bool = True
    tenant_id: str | None = None


BUILTIN_DOMAINS: dict[str, DomainDefinition] = {
    "legal": DomainDefinition(
        slug="legal",
        label="Pháp luật",
        description="Văn bản pháp luật, nghị định, thông tư và quy định.",
        hints=(
            "luật",
            "bộ luật",
            "nghị định",
            "thông tư",
            "quyết định",
            "điều khoản pháp lý",
            "legal",
            "regulation",
        ),
    ),
    "contract": DomainDefinition(
        slug="contract",
        label="Hợp đồng",
        description="Hợp đồng, thỏa thuận và điều khoản giữa các bên.",
        hints=(
            "hợp đồng",
            "thỏa thuận",
            "bên a",
            "bên b",
            "điều khoản",
            "cam kết",
            "contract",
            "agreement",
        ),
    ),
    "scientific": DomainDefinition(
        slug="scientific",
        label="Khoa học",
        description="Bài báo, nghiên cứu khoa học và tài liệu học thuật.",
        hints=(
            "nghiên cứu",
            "bài báo khoa học",
            "phương pháp",
            "kết quả nghiên cứu",
            "tài liệu tham khảo",
            "học thuật",
            "research",
            "abstract",
        ),
    ),
    "financial": DomainDefinition(
        slug="financial",
        label="Tài chính",
        description="Báo cáo tài chính, kế toán và số liệu tài chính.",
        hints=(
            "tài chính",
            "báo cáo tài chính",
            "kế toán",
            "doanh thu",
            "lợi nhuận",
            "bảng cân đối",
            "financial",
            "revenue",
        ),
    ),
    "technical": DomainDefinition(
        slug="technical",
        label="Kỹ thuật",
        description="Tài liệu kỹ thuật, hướng dẫn vận hành và thiết kế.",
        hints=(
            "kỹ thuật",
            "hướng dẫn",
            "vận hành",
            "thiết kế",
            "api",
            "cài đặt",
            "technical",
            "manual",
        ),
    ),
    "medical": DomainDefinition(
        slug="medical",
        label="Y tế",
        description="Hồ sơ bệnh án, hướng dẫn điều trị và tài liệu y tế.",
        hints=(
            "y tế",
            "bệnh",
            "triệu chứng",
            "điều trị",
            "thuốc",
            "bệnh nhân",
            "medical",
            "clinical",
        ),
    ),
    "business": DomainDefinition(
        slug="business",
        label="Kinh doanh",
        description="Tài liệu kinh doanh, thị trường và chiến lược.",
        hints=(
            "kinh doanh",
            "thị trường",
            "khách hàng",
            "chiến lược",
            "marketing",
            "doanh nghiệp",
            "business",
            "sales",
        ),
    ),
    "general": DomainDefinition(
        slug="general",
        label="Tổng hợp",
        description="Tài liệu tổng hợp hoặc chưa xác định lĩnh vực.",
        hints=(
            "tổng hợp",
            "tài liệu chung",
            "ghi chú",
            "miscellaneous",
            "general",
        ),
    ),
}

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def normalize_domain_slug(value: str) -> str:
    """Lowercase, ASCII-only, hyphen-separated slug.

    Vietnamese diacritics are folded (``Hợp đồng`` -> ``hop-dong``) so a
    label can safely be used as a default slug.
    """
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.replace("đ", "d").replace("Đ", "D").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:64].strip("-")


def is_valid_domain_slug(value: str) -> bool:
    return bool(value) and len(value) <= 64 and _SLUG_RE.match(value) is not None


def domain_options(custom: list[DomainDefinition]) -> list[DomainDefinition]:
    """Built-ins first, then custom domains that do not shadow a built-in."""
    options = list(BUILTIN_DOMAINS.values())
    seen = set(BUILTIN_DOMAINS)
    for item in custom:
        slug = normalize_domain_slug(item.slug)
        if not slug or slug in seen:
            continue
        seen.add(slug)
        if slug != item.slug:
            item = replace(item, slug=slug)
        options.append(item)
    return options
