"""Chrome helpers shared by crawl_mgt, detect_mgt and probe_mgt."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, WebDriverException
from selenium.webdriver import ActionChains
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement

from common.contracts import RunConfig
from configuration.settings import Settings

DriverFactory = Callable[[RunConfig], WebDriver]


def make_driver_factory(settings: Settings, prompt_behavior: str = "dismiss") -> DriverFactory:
    """prompt_behavior: how Selenium treats native dialogs nobody asked about.
    Crawl/detect use "dismiss" (never block on a page's alert); probe uses "ignore" and
    handles dialogs itself."""

    def factory(cfg: RunConfig) -> WebDriver:
        opts = Options()
        if settings.chrome_binary:
            opts.binary_location = settings.chrome_binary
        if cfg.headless:
            opts.add_argument("--headless=new")
        opts.add_argument(f"--window-size={cfg.viewport_width},{cfg.viewport_height}")
        opts.add_argument("--force-device-scale-factor=1")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--hide-scrollbars")
        opts.set_capability("unhandledPromptBehavior", prompt_behavior)
        opts.page_load_strategy = "normal"
        driver = webdriver.Chrome(options=opts)
        driver.set_page_load_timeout(cfg.page_load_timeout_s)
        driver.set_script_timeout(cfg.page_load_timeout_s)
        return driver

    return factory


def safe_quit(driver: WebDriver | None) -> None:
    if driver is not None:
        try:
            driver.quit()
        except Exception:  # noqa: BLE001
            pass


def wait_ready(driver: WebDriver, settle_ms: int, timeout_s: float = 15) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if driver.execute_script("return document.readyState") == "complete":
                break
        except WebDriverException:
            pass
        time.sleep(0.1)
    time.sleep(settle_ms / 1000)


def find(driver: WebDriver, selector: str) -> WebElement | None:
    try:
        return driver.find_element("css selector", selector)
    except (NoSuchElementException, WebDriverException):
        return None


def replay(driver: WebDriver, steps: list[dict[str, Any]], settle_ms: int) -> str | None:
    """Re-create the page state in which a hidden control was found (open a menu, hover a row...).
    Returns an error string if a step cannot be performed, else None."""
    for step in steps:
        el = find(driver, step["selector"])
        if el is None:
            return f"step target not found: {step['selector']}"
        try:
            if step["action"] == "hover":
                ActionChains(driver).move_to_element(el).perform()
            else:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'})", el)
                try:
                    el.click()
                except WebDriverException:
                    driver.execute_script("arguments[0].click()", el)
        except WebDriverException as exc:
            return f"step failed: {step['action']} {step['selector']}: {exc.msg or exc}"
        time.sleep(settle_ms / 1000)
    return None
