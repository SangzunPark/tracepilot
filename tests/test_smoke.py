import importlib


def test_tracepilot_package_is_importable() -> None:
    module = importlib.import_module("tracepilot")

    assert module.__name__ == "tracepilot"
