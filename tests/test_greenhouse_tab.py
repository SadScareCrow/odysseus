from pathlib import Path

ROOT = Path(__file__).parents[1]
MODULE = ROOT / "static/js/greenhouse.js"


def test_greenhouse_module_exists_without_greenhouse_vocabulary():
    source = MODULE.read_text(encoding="utf-8")
    forbidden = ["aquariums", "about-daniel", "tools-and-workshop", "fact", "preference", "open loop", "intention", "hypothesis", "accepted", "archived"]
    assert not any(word in source.lower() for word in forbidden)


def test_greenhouse_wiring_and_markup_are_present():
    app = (ROOT / "static/app.js").read_text(encoding="utf-8")
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    assert "./js/greenhouse.js" in app
    assert "'/greenhouse'" in app
    assert "tool-greenhouse-btn" in app
    assert "greenhouse-modal" in html
    assert "rail-greenhouse" in html


def test_greenhouse_module_fetches_once_and_filters_locally():
    source = MODULE.read_text(encoding="utf-8")
    assert "v1/memories?limit=500" in source
    assert source.count("fetch(") == 1
    assert "filter(" in source
    assert "area_key" in source
    assert "uiModule.esc" in source


def test_greenhouse_module_has_visible_failure_states_and_search():
    source = MODULE.read_text(encoding="utf-8")
    assert "Unable to reach Greenhouse" in source
    assert "not configured" in source.lower()
    assert "memories/search" not in source
    assert "search" in source.lower()
