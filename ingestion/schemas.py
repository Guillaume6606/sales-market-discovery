from dataclasses import dataclass


@dataclass
class ProductTemplateSnapshot:
    product_id: str
    name: str
    description: str | None
    search_query: str
    category_id: str
    category_name: str | None
    brand: str | None
    price_min: float | None
    price_max: float | None
    providers: list[str]
    words_to_avoid: list[str]
    enable_llm_validation: bool
    is_active: bool
