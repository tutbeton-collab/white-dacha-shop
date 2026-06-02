#!/usr/bin/env python3
"""Take screenshots of both app variants"""
import asyncio
from playwright.async_api import async_playwright

async def screenshot():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        
        # V2 screenshot
        page = await browser.new_page(viewport={'width': 390, 'height': 844})
        await page.goto('file:///home/ser/white-dacha-shop/v2/index.html')
        await page.wait_for_timeout(2000)
        await page.screenshot(path='/home/ser/white-dacha-shop/screenshots/v2.png', full_page=False)
        print("V2 screenshot saved")
        
        # V3 screenshot
        page2 = await browser.new_page(viewport={'width': 390, 'height': 844})
        await page2.goto('file:///home/ser/white-dacha-shop/v3/index.html')
        await page2.wait_for_timeout(2000)
        await page2.screenshot(path='/home/ser/white-dacha-shop/screenshots/v3.png', full_page=False)
        print("V3 screenshot saved")
        
        await browser.close()

asyncio.run(screenshot())
