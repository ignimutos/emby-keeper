import importlib
import warnings

from pydantic import ValidationError
from pydantic.warnings import PydanticDeprecatedSince20

from embykeeper.schema import (
    Config,
    EmbyAccount,
    EmbyConfig,
    format_errors,
)


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


# --- 别名与向后兼容字段 ---


def test_config_accepts_emby_list_alias():
    model = Config.model_validate(
        {"emby": [{"url": "https://example.com", "username": "u", "password": "p", "ua": "Custom/1.0"}]}
    )
    assert model.emby.account[0].useragent == "Custom/1.0"


def test_config_accepts_notifier_string_alias():
    model = Config.model_validate({"notifier": "tgram://bot/chat"})
    assert model.notifier.enabled is True
    assert model.notifier.apprise_uri == "tgram://bot/chat"


def test_config_accepts_notifier_bool_alias():
    model = Config.model_validate({"notifier": True})
    assert model.notifier.enabled is True


def test_config_maps_legacy_alias_fields():
    model = Config.model_validate({"watchtime": "<9:00AM,10:00AM>", "interval": 5})
    assert model.emby.time_range == "<9:00AM,10:00AM>"
    assert model.emby.interval_days == "5"  # UseStr 强制为字符串


# --- 类型强制 (UseStr / UseHttpUrl) ---


def test_emby_config_use_str_coerces_numbers():
    model = EmbyConfig(time_range=10, interval_days=5)
    assert model.time_range == "10"
    assert model.interval_days == "5"


def test_emby_account_use_http_url_adds_scheme():
    account = EmbyAccount(url="example.com", username="u", password="p")
    assert str(account.url).startswith("https://example.com")


# --- format_errors ---


def test_format_errors_required_field_translated():
    try:
        EmbyAccount(username="u", password="p")
        assert False, "应抛出 ValidationError"
    except ValidationError as exc:
        text = format_errors(exc)
    assert "配置文件错误" in text
    assert "必填字段" in text
    assert "url" in text


def test_format_errors_reports_unknown_extra_field():
    try:
        Config.model_validate({"bogus_field": 1})
        assert False, "应拒绝未知字段"
    except ValidationError as exc:
        text = format_errors(exc)
    assert "配置文件错误" in text
    assert "包含未知设置项" in text


def test_use_http_url_str():
    from embykeeper.schema import UseHttpUrl

    url = UseHttpUrl("https://example.com")
    assert str(url) == "https://example.com/"


def test_format_errors_groups_legacy_alias():
    try:
        Config.model_validate({"watch_concurrent": "not-an-int"})
        assert False, "应抛 ValidationError"
    except ValidationError as exc:
        text = format_errors(exc)
    assert "emby -> concurrency" in text
    assert "旧版本为" in text
