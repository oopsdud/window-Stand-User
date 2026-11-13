import copy
import json

import pytest
from PIL import Image

from desktop_app.settings import DEFAULTS, SettingsStore, asset_path, import_art, validate_model


def test_settings_round_trip_encrypts_key(tmp_path):
    store = SettingsStore(tmp_path)
    config = copy.deepcopy(DEFAULTS)
    config.update(model="a-model", api_key="secret-never-store-as-plaintext", pet_name="小芽")
    config["planner"]["api_key"] = "planner-secret-never-store"
    config["vision_model"]["api_key"] = "vision-secret-never-store"
    store.save(config)
    assert "secret-never-store-as-plaintext" not in store.path.read_text(encoding="utf-8")
    assert "planner-secret-never-store" not in store.path.read_text(encoding="utf-8")
    assert "vision-secret-never-store" not in store.path.read_text(encoding="utf-8")
    assert store.load() == config


def test_corrupt_settings_are_preserved_and_reported(tmp_path):
    store = SettingsStore(tmp_path)
    store.path.write_text("broken json", encoding="utf-8")
    assert store.load()["model"] == ""
    assert store.warning
    assert store.path.read_text() == "broken json"


def test_layout_autosave_preserves_newer_role_settings(tmp_path, monkeypatch):
    import desktop_app.settings as settings

    monkeypatch.setattr(settings, "_dpapi", lambda data, decrypt=False: data[::-1])
    old_store = SettingsStore(tmp_path)
    old_store.save(copy.deepcopy(DEFAULTS))
    config = SettingsStore(tmp_path).load()
    config["planner"].update(enabled=True, model="new-brain", api_key="saved-key")
    SettingsStore(tmp_path).save(config)
    old_store.save_layout({"position": [-1000, 200], "pet_size": 180})
    restarted = SettingsStore(tmp_path).load()
    assert restarted["planner"] == config["planner"]
    assert restarted["position"] == [-1000, 200]
    old_store.path.write_text("invalid-json", encoding="utf-8")
    with pytest.raises(OSError, match="停止自动覆盖"):
        old_store.save_layout({"position": [0, 0]})
    assert old_store.path.read_text() == "invalid-json"


def test_first_launch_without_cli_config_has_no_warning(tmp_path):
    store = SettingsStore(tmp_path)
    assert store.load() == DEFAULTS
    assert not store.warning


def test_invalid_geometry_is_repaired(tmp_path):
    store = SettingsStore(tmp_path)
    store.path.write_text(json.dumps({"pet_size": 10000, "position": ["bad"], "max_steps": -5}))
    config = store.load()
    assert config["pet_size"] == 220
    assert config["position"] is None
    assert config["max_steps"] == 1


def test_animated_import_retains_frames_and_duration(tmp_path):
    source = tmp_path / "source.gif"
    frames = [Image.new("RGBA", (500, 300), color) for color in ("red", "blue")]
    frames[0].save(source, save_all=True, append_images=frames[1:], duration=[90, 170], loop=0)
    relative = import_art(str(source), tmp_path)
    path = asset_path(relative, tmp_path)
    with Image.open(path) as result:
        assert result.n_frames == 2
        assert result.width == 500
        result.seek(1)
        assert result.info["duration"] == 170
    source.unlink()
    assert path.exists()  # The pet does not depend on the original upload location.


def test_static_import_keeps_alpha(tmp_path):
    source = tmp_path / "transparent.png"
    Image.new("RGBA", (40, 40), (255, 0, 0, 90)).save(source)
    with Image.open(asset_path(import_art(str(source), tmp_path), tmp_path)) as result:
        assert result.convert("RGBA").getpixel((5, 5))[3] == 90


def test_white_background_removed_but_enclosed_white_detail_preserved(tmp_path):
    from PIL import ImageDraw

    from desktop_app.settings import remove_white_background

    source = Image.new("RGBA", (100, 100), "white")
    draw = ImageDraw.Draw(source)
    draw.ellipse((20, 20, 80, 80), fill="red")
    draw.ellipse((40, 40, 60, 60), fill="white")
    result = remove_white_background(source)
    assert result.getpixel((0, 0))[3] == 0
    assert result.getpixel((50, 50)) == (255, 255, 255, 255)
    assert result.getpixel((30, 50))[3] == 255
    path = tmp_path / "white.png"
    source.save(path)
    with Image.open(asset_path(import_art(str(path), tmp_path), tmp_path)) as cropped:
        assert cropped.size == (61, 61)
    with Image.open(
        asset_path(import_art(str(path), tmp_path, remove_white=False), tmp_path)
    ) as unchanged:
        assert unchanged.size == (100, 100)
        assert unchanged.convert("RGBA").getpixel((0, 0))[3] == 255


def test_high_resolution_import_is_not_reduced_to_old_320_limit(tmp_path):
    path = tmp_path / "large.png"
    Image.new("RGB", (1200, 1000), "blue").save(path)
    with Image.open(asset_path(import_art(str(path), tmp_path), tmp_path)) as result:
        assert result.width == 1024


def test_fully_white_image_has_actionable_error(tmp_path):
    path = tmp_path / "white.png"
    Image.new("RGB", (20, 20), "white").save(path)
    with pytest.raises(ValueError, match="透明"):
        import_art(str(path), tmp_path)


def test_assets_cannot_reference_arbitrary_files(tmp_path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"not an asset")
    assert asset_path(str(outside), tmp_path) is None
    assert asset_path("../outside.png", tmp_path) is None


@pytest.mark.parametrize("address", ["file:///c:/a", "hello", "https://user:password@example.com"])
def test_invalid_model_addresses_rejected(address):
    with pytest.raises(ValueError):
        validate_model({"provider": "openai", "model": "test", "base_url": address})
