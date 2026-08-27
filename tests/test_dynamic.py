"""dynamic.py 分发契约测试: 显式 __export__ 优先, 约定回退, 错配响亮警告."""

import types
from types import SimpleNamespace

import embykeeper.telegram.dynamic as dynamic


class Recorder:
    def __init__(self):
        self.warnings = []

    def warning(self, message):
        self.warnings.append(message)


def make_module(classes=None, export=None):
    mod = types.ModuleType("fake_site")
    for cls in classes or []:
        setattr(mod, cls.__name__, cls)
    if export is not None:
        mod.__export__ = export
    return mod


def patch_import(monkeypatch, module):
    monkeypatch.setattr(dynamic, "import_module", lambda _path: module)


def test_get_cls_uses_explicit_export(monkeypatch):
    class MySiteCheckin:
        pass

    patch_import(monkeypatch, make_module(export=[MySiteCheckin]))

    result = dynamic.get_cls("checkiner", names=["mysite"])

    assert result == [MySiteCheckin]


def test_get_cls_falls_back_to_naming_convention(monkeypatch):
    class MySiteCheckin:
        pass

    patch_import(monkeypatch, make_module(classes=[MySiteCheckin]))

    result = dynamic.get_cls("checkiner", names=["mysite"])

    assert result == [MySiteCheckin]


def test_get_cls_warns_and_skips_on_invalid_export(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(dynamic, "logger", recorder)
    patch_import(monkeypatch, make_module(export="not-a-list"))

    result = dynamic.get_cls("checkiner", names=["mysite"])

    assert result == []
    assert any("__export__" in message for message in recorder.warnings)


def test_get_cls_warns_when_no_class_found(monkeypatch):
    class Unrelated:
        pass

    recorder = Recorder()
    monkeypatch.setattr(dynamic, "logger", recorder)
    patch_import(monkeypatch, make_module(classes=[Unrelated]))

    result = dynamic.get_cls("checkiner", names=["mysite"])

    assert result == []
    assert any("未找到合法的类" in message for message in recorder.warnings)


def test_ignore_marker_detected_via_source(tmp_path):
    ignored = tmp_path / "ignored.py"
    ignored.write_text("__ignore__ = True\n")
    assert dynamic._module_has_ignore_marker(ignored) is True

    normal = tmp_path / "normal.py"
    normal.write_text("class Foo:\n    pass\n")
    assert dynamic._module_has_ignore_marker(normal) is False


def test_extract_expands_nested_classes():
    class Outer:
        class Inner:
            pass

    assert dynamic.extract([Outer]) == [Outer.Inner]
