# Pydantic

## When this applies
Writing Pydantic models for validation, serialization, or API schemas.

## Patterns / Gotchas (v2 specific)
- Pydantic v2 is a complete rewrite — v1 APIs (`.dict()`, `.json()`, `@validator`) are deprecated but still work with warnings
- Use `.model_dump()` not `.dict()`, `.model_json_schema()` not `.schema()`, `model_validate()` not `parse_obj()`
- `@field_validator` replaces `@validator` — different signature: `@field_validator('field_name') @classmethod def validate(cls, v):`
- `@model_validator(mode='before')` runs before field validation — receives raw dict. `mode='after'` runs after and receives model instance
- `model_config = ConfigDict(strict=True)` — strict mode rejects type coercion (no `"123"` → `123`). Default is permissive
- `from_attributes=True` (was `orm_mode=True`) — required for creating models from ORM objects/dataclasses
- `Field(exclude=True)` excludes field from serialization but it's still required on input — use `Field(default=None, exclude=True)` for optional excluded fields
- `Annotated[int, Field(gt=0)]` syntax works and is preferred over `Field()` in class body for reusable types
- `TypeAdapter` replaces standalone `parse_obj_as()` — `TypeAdapter(list[int]).validate_python([1,2,3])`
- JSON Schema generation: `Optional[X]` generates `anyOf: [{type: X}, {type: null}]` — some frontend generators struggle with this; use `X | None` for cleaner output (same runtime behavior)
- Recursive models: v2 handles them correctly but `model_json_schema()` uses `$defs` — some OpenAPI tools need `--dereference` to inline them
- `model_validate_json()` is faster than `model_validate(json.loads(data))` — it skips the intermediate dict
