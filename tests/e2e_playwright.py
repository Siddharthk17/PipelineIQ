import asyncio
from playwright.async_api import async_playwright, expect

async def run_test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # 1. Registration
        print("Testing Registration...")
        await page.goto("http://localhost/register")
        await page.fill('input[name="email"]', "e2e_user@example.com")
        await page.fill('input[name="username"]', "e2e_user")
        await page.fill('input[name="password"]', "Password123!")
        await page.click('button[type="submit"]')
        await expect(page).to_have_url("http://localhost/login")
        print("Registration successful.")

        # 2. Login
        print("Testing Login...")
        await page.fill('input[name="email"]', "e2e_user@example.com")
        await page.fill('input[name="password"]', "Password123!")
        await page.click('button[type="submit"]')
        await expect(page).to_have_url("http://localhost/dashboard")
        print("Login successful.")

        # 3. File Upload
        print("Testing File Upload...")
        # We need a file to upload
        with open("e2e_test_file.csv", "w") as f:
            f.write("id,val\n1,10\n2,20\n")
        
        # Open File Upload Widget
        # Note: Widgets are in a binary tree layout, we might need to find the widget by text
        await page.click("text=File Upload")
        
        # Upload file
        async with page.expect_file_chooser() as fc_info:
            await page.click("text=Choose File")
        file_chooser = await fc_info.value
        await file_chooser.set_files("e2e_test_file.csv")
        
        # Wait for upload success
        await expect(page.locator("text=Upload Successful")).to_be_visible()
        print("File upload successful.")

        # 4. Pipeline Execution
        print("Testing Pipeline Execution...")
        await page.click("text=Pipeline Editor")
        
        yaml_config = """
pipeline:
  name: e2e_test
  steps:
    - name: load
      type: load
      file_id: a-uuid-from-upload
    - name: save
      type: save
      input: load
      filename: e2e_out
"""
        # We need the actual file_id. We'll get it from the File Registry.
        await page.click("text=File Registry")
        file_id_element = await page.locator(".file-id").first.inner_text()
        file_id = file_id_element.strip()
        
        # Update YAML with real file_id
        yaml_config = yaml_config.replace("a-uuid-from-upload", file_id)
        
        await page.click("text=Pipeline Editor")
        # Use CodeMirror editor. We might need to click it first.
        await page.click(".cm-editor")
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        await page.keyboard.type(yaml_config)
        
        # Run pipeline
        await page.click("text=Run Pipeline")
        
        # Monitor progress
        await page.click("text=Run Monitor")
        await expect(page.locator("text=COMPLETED")).to_be_visible(timeout=60000)
        print("Pipeline execution successful.")

        # 5. Lineage View
        print("Testing Lineage Graph...")
        await page.click("text=Lineage Graph")
        # Check if nodes are rendered
        await expect(page.locator(".react-flow__node")).to_be_visible()
        print("Lineage graph rendered.")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_test())
