#!/usr/bin/env python3
"""Complete OTP sandbox payment SCA and hit ERP callback."""

from __future__ import annotations

import re
import sys
from urllib.parse import urlparse, parse_qs

from playwright.sync_api import sync_playwright

SCA_URL = (
    'https://iam.sandbox.otpbanka.hr/payment?'
    'transferId=7bdbf508e4224026b89c6869290a4425&'
    'authorizationId=c8b0afda05704fb891625df134097b91&'
    'returnUrl=https://otp-sbx.racunai.hr/oauth/callback/'
)
PSU_USER = '166.company.no1'
PSU_PASS = 'Pexim.1'


def main() -> int:
    sca_url = sys.argv[1] if len(sys.argv) > 1 else SCA_URL
    confirmation_code: str | None = None
    final_url: str | None = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(sca_url, wait_until='networkidle', timeout=120_000)

        # Login form (OTP IAM)
        for selector in ('input[name="Username"]', 'input#Username', 'input[type="text"]'):
            if page.locator(selector).count():
                page.fill(selector, PSU_USER)
                break
        for selector in ('input[name="Password"]', 'input#Password', 'input[type="password"]'):
            if page.locator(selector).count():
                page.fill(selector, PSU_PASS)
                break
        for selector in (
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Prijaviti")',
            'button:has-text("Prijava")',
        ):
            if page.locator(selector).count():
                page.click(selector)
                break

        page.wait_for_timeout(2000)

        # Sandbox payment approval screen
        for selector in (
            'button:has-text("Odobriti")',
            'a:has-text("Odobriti")',
            'input[value="Odobriti"]',
            'button:has-text("Potvrdi")',
            'button:has-text("Confirm")',
            'button:has-text("Autoriziraj")',
            'button:has-text("Authorize")',
        ):
            loc = page.locator(selector)
            if loc.count():
                loc.first.click()
                page.wait_for_timeout(2000)
                break

        try:
            page.wait_for_url(re.compile(r'otp-sbx\.racunai\.hr|otp-company-no1\.racunai\.hr'), timeout=90_000)
        except Exception:
            pass

        final_url = page.url
        print('FINAL_URL:', final_url)
        print('TITLE:', page.title())
        body = page.inner_text('body')[:500]
        print('BODY:', body)

        parsed = urlparse(final_url)
        qs = parse_qs(parsed.query)
        if 'confirmationCode' in qs:
            confirmation_code = qs['confirmationCode'][0]
            print('CONFIRMATION_CODE:', confirmation_code)

        browser.close()

    if confirmation_code:
        import requests

        callback = f'https://otp-sbx.racunai.hr/oauth/callback/?confirmationCode={confirmation_code}'
        resp = requests.get(callback, allow_redirects=False, timeout=60)
        print('CALLBACK_HTTP:', resp.status_code)
        print('CALLBACK_LOCATION:', resp.headers.get('Location', ''))
        if resp.status_code >= 400:
            print('CALLBACK_BODY:', resp.text[:500])
            return 1
        return 0

    if final_url and 'paymentorder' in final_url:
        print('SCA callback handled by ERP (redirect to admin).')
        return 0

    print('ERROR: No confirmationCode in redirect URL', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
