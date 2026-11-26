"""Render every Streamlit page headlessly (simulated data) and assert no exceptions."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

PAGES = [
    ("ui.pages.overview", "dashboard"), ("ui.pages.overview", "market_overview"),
    ("ui.pages.scanner", "scanner_page"), ("ui.pages.screenshots", "screenshot_page"),
    ("ui.pages.scanner", "combined_page"), ("ui.pages.scanner", "top3_page"),
    ("ui.pages.analysis", "chart_page"), ("ui.pages.analysis", "strategy_page"),
    ("ui.pages.trading", "risk_page"), ("ui.pages.trading", "paper_page"), ("ui.pages.backtest_page", "backtest_page"),
    ("ui.pages.trading", "journal_page"), ("ui.pages.trading", "performance_page"),
    ("ui.pages.settings_page", "settings_page"),
]

SCRIPT = """
import sys
sys.path.insert(0, {root!r})
from ui import state
from ui.components.widgets import inject_css
state.init_app()
inject_css()
from {mod} import {fn}
{fn}()
"""


@pytest.fixture
def ui_env(tmp_path, monkeypatch):
    monkeypatch.setenv("BT_DATA_PROVIDER", "simulated")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'ui.sqlite').as_posix()}")


@pytest.mark.parametrize("mod,fn", PAGES)
def test_page_renders(ui_env, mod, fn):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_string(SCRIPT.format(root=str(ROOT), mod=mod, fn=fn), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]


def test_dashboard_scan_button_runs_pipeline(ui_env):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_string(SCRIPT.format(root=str(ROOT), mod="ui.pages.overview", fn="dashboard"),
                             default_timeout=300)
    at.run()
    at.session_state["settings"].scan.max_symbols_per_scan = 6
    at.button[0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["last_scan"] is not None
