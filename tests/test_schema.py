import importlib
import warnings

from pydantic.warnings import PydanticDeprecatedSince20


def test_schema_import_avoids_pydantic_v1_validator_deprecation():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", PydanticDeprecatedSince20)
        import embykeeper.schema as schema_module

        importlib.reload(schema_module)

    deprecations = [
        warning
        for warning in caught
        if isinstance(warning.message, PydanticDeprecatedSince20)
        and "__get_validators__" in str(warning.message)
    ]

    assert deprecations == []
