import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from autofill.profile import load_profile

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def profile():
    return load_profile(ROOT / "profile.example.yaml")


@pytest.fixture(scope="session")
def browser():
    # 用本机已装的 Edge/Chrome，不需要 playwright install 下载浏览器
    with sync_playwright() as p:
        b = p.chromium.launch(channel=os.environ.get("AUTOFILL_TEST_BROWSER", "msedge"), headless=True,
                             chromium_sandbox=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    pg = browser.new_page()
    pg.on("dialog", lambda d: d.dismiss())
    yield pg
    pg.close()


def demo_url(name: str) -> str:
    return (ROOT / "demo" / f"{name}.html").as_uri()
