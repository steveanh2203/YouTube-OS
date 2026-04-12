"""YouTube Community DOM publisher via Roxy + Selenium."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from autocapcut.services.roxy_upload import (
    RoxyApiClient,
    RoxyUploadError,
    _capture_debug_bundle,
    _normalize_debugger_address,
    _require_selenium,
)


class CommunityDomPublishError(RuntimeError):
    """Raised when Community DOM automation cannot complete safely."""


@dataclass(slots=True)
class CommunityPublishSummary:
    """Result from a successful Community publish action."""

    debugger_address: str
    youtube_post_url: str | None = None
    youtube_post_id: str | None = None


def normalize_channel_posts_url(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise CommunityDomPublishError("Channel URL is required before publishing.")
    if "://" not in value:
        value = f"https://{value}"
    value = value.rstrip("/")
    if not value.endswith("/posts"):
        value = f"{value}/posts"
    return value


def _find_first_visible_xpath(driver, by, xpaths: list[str]):
    for xpath in xpaths:
        for node in driver.find_elements(by.XPATH, xpath):
            try:
                if node.is_displayed():
                    return node
            except Exception:
                continue
    return None


def _find_textbox(driver):
    script = """
const isVisible = (el) => {
  if (!el) return false;
  const style = window.getComputedStyle(el);
  const rect = el.getBoundingClientRect();
  return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 40 && rect.height > 24;
};
const roots = [document];
while (roots.length) {
  const root = roots.shift();
  const nodes = root.querySelectorAll
    ? root.querySelectorAll("div[contenteditable='true'], yt-formatted-string[contenteditable='true'], [role='textbox'][contenteditable='true']")
    : [];
  for (const node of nodes) {
    if (isVisible(node)) return node;
  }
  const descendants = root.querySelectorAll ? root.querySelectorAll("*") : [];
  for (const node of descendants) {
    if (node.shadowRoot) roots.push(node.shadowRoot);
  }
}
return null;
"""
    try:
        return driver.execute_script(script)
    except Exception:
        return None


def _find_image_input(driver):
    script = """
const roots = [document];
while (roots.length) {
  const root = roots.shift();
  const nodes = root.querySelectorAll ? root.querySelectorAll("input[type='file']") : [];
  for (const node of nodes) {
    const accept = (node.getAttribute('accept') || '').toLowerCase();
    if (accept.includes('image')) return node;
  }
  for (const node of nodes) {
    if (!node.disabled) return node;
  }
  const descendants = root.querySelectorAll ? root.querySelectorAll('*') : [];
  for (const node of descendants) {
    if (node.shadowRoot) roots.push(node.shadowRoot);
  }
}
return null;
"""
    try:
        return driver.execute_script(script)
    except Exception:
        return None


def _open_composer(driver, by) -> None:
    trigger = _find_first_visible_xpath(driver, by, [
        "//*[contains(normalize-space(.), 'Share a behind-the-scenes photo')]",
        "//*[contains(normalize-space(.), 'Share an update')]",
        "//*[contains(normalize-space(.), 'Create a post')]",
        "//*[contains(normalize-space(.), 'Write a post')]",
        "//*[contains(normalize-space(.), 'Image')]",
        "//*[@aria-label='Create a post']",
    ])
    if trigger is not None:
        driver.execute_script("arguments[0].click();", trigger)
        time.sleep(1.0)


def _set_textbox_value(textbox, body: str) -> None:
    try:
        textbox.click()
    except Exception:
        pass
    textbox.send_keys(body)


def _attach_image_if_needed(driver, by, image_path: Path) -> None:
    image_button = _find_first_visible_xpath(driver, by, [
        "//*[normalize-space(.)='Image']",
        "//*[contains(@aria-label, 'Image')]",
        "//*[contains(normalize-space(.), 'Add image')]",
    ])
    if image_button is not None:
        try:
            driver.execute_script("arguments[0].click();", image_button)
            time.sleep(0.8)
        except Exception:
            pass

    file_input = _find_image_input(driver)
    if file_input is None:
        raise CommunityDomPublishError("Could not find the image file input on the Community page.")
    file_input.send_keys(str(image_path.resolve()))
    time.sleep(1.6)


def _find_post_button(driver, by):
    return _find_first_visible_xpath(driver, by, [
        "//button[normalize-space(.)='Post']",
        "//*[self::button or @role='button'][normalize-space(.)='Post']",
        "//*[self::button or @role='button'][normalize-space(.)='Đăng']",
        "//*[self::button or @role='button'][contains(@aria-label, 'Post')]",
    ])


def _verify_publish_transition(driver, by, timeout_sec: float = 8.0) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        post_button = _find_post_button(driver, by)
        textbox = _find_textbox(driver)
        if post_button is None and textbox is None:
            return
        time.sleep(0.35)
    raise CommunityDomPublishError("Could not verify that the post was published safely.")


def publish_community_post_via_roxy(
    *,
    api_host: str,
    api_token: str,
    workspace_id: int,
    profile_id: str,
    channel_url: str,
    body: str,
    image_path: str | None = None,
    debug_root: Path | None = None,
) -> CommunityPublishSummary:
    """Open the target channel posts page and publish a Community post."""
    client = RoxyApiClient(api_host, api_token)
    open_data = client.open_profile(
        workspace_id=workspace_id,
        profile_id=profile_id.strip(),
        force_open=True,
    )
    debugger_address = _normalize_debugger_address(str(open_data.get("http") or ""))
    driver_path = str(open_data.get("driver") or "").strip()

    if not debugger_address:
        raise CommunityDomPublishError("Roxy did not return a debugger address for this profile.")
    if not driver_path:
        raise CommunityDomPublishError("Roxy did not return a Selenium driver path for this profile.")

    webdriver, service_cls, by = _require_selenium()
    options = webdriver.ChromeOptions()
    options.add_experimental_option("debuggerAddress", debugger_address)
    service = service_cls(driver_path)
    driver = None

    try:
        driver = webdriver.Chrome(service=service, options=options)
    except Exception as exc:
        raise CommunityDomPublishError("Could not attach Selenium to the opened Roxy browser.") from exc

    target_url = normalize_channel_posts_url(channel_url)
    logger.info("Opening Community page: {}", target_url)

    try:
        driver.get(target_url)
        time.sleep(2.2)

        current_url = (driver.current_url or "").rstrip("/")
        expected_prefix = target_url[:-6] if target_url.endswith("/posts") else target_url
        if not current_url.startswith(expected_prefix):
            raise CommunityDomPublishError(
                f"Opened the wrong channel/page. Expected prefix: {expected_prefix}. Current URL: {current_url}"
            )
        if "accounts.google.com" in current_url or "youtube.com/signin" in current_url:
            raise CommunityDomPublishError("This Roxy profile is not signed into YouTube.")

        _open_composer(driver, by)
        textbox = _find_textbox(driver)
        if textbox is None:
            raise CommunityDomPublishError("Could not find the Community post textbox.")

        _set_textbox_value(textbox, body.strip())
        time.sleep(0.8)

        if image_path:
            resolved_image = Path(image_path)
            if not resolved_image.exists() or not resolved_image.is_file():
                raise CommunityDomPublishError(f"Image file does not exist: {image_path}")
            _attach_image_if_needed(driver, by, resolved_image)

        post_button = _find_post_button(driver, by)
        if post_button is None:
            raise CommunityDomPublishError("Could not find the Post button on the Community page.")

        driver.execute_script("arguments[0].click();", post_button)
        time.sleep(1.0)
        _verify_publish_transition(driver, by)
    except (CommunityDomPublishError, RoxyUploadError):
        if driver is not None:
            _capture_debug_bundle(driver, reason="community-post", debug_root=debug_root)
        raise
    except Exception as exc:
        bundle = _capture_debug_bundle(driver, reason="community-post", debug_root=debug_root) if driver is not None else None
        message = str(exc)
        if bundle is not None:
            message = f"{message}\nDebug bundle: {bundle}"
        raise CommunityDomPublishError(message) from exc
    finally:
        if driver is not None:
            try:
                driver.service.stop()
            except Exception:
                pass

    match = re.search(r"/(post|community/)([^/?#]+)", (driver.current_url if driver is not None else "") or "")
    post_id = match.group(2) if match else None
    return CommunityPublishSummary(
        debugger_address=debugger_address,
        youtube_post_url=((driver.current_url if driver is not None else "") or target_url),
        youtube_post_id=post_id,
    )
