import { chromium } from '../../frontend/node_modules/playwright/index.mjs'

const baseUrl = process.env.ANNOTATION_BASE_URL || 'http://127.0.0.1:8091'
const accessCode = process.env.ANNOTATION_ACCESS_CODE
if (!accessCode) throw new Error('ANNOTATION_ACCESS_CODE is required')

const browser = await chromium.launch({ headless: true })

async function checkViewport(name, viewport, output) {
  const context = await browser.newContext({ viewport })
  const page = await context.newPage()
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded' })
  await page.locator('#login-form').waitFor({ state: 'visible' })
  await page.locator('#annotator-id').fill(`visual_${name}`)
  await page.locator('#access-code').fill(accessCode)
  await page.locator('#login-form button[type="submit"]').click()
  await page.locator('#annotation-form').waitFor({ state: 'visible' })
  const layout = await page.evaluate(() => ({
    viewportWidth: window.innerWidth,
    documentWidth: document.documentElement.scrollWidth,
    taskCount: document.querySelectorAll('.task-item').length,
    formWidth: document.querySelector('#annotation-form')?.getBoundingClientRect().width || 0,
    overflowElements: [...document.querySelectorAll('body *')]
      .map(element => ({
        tag: element.tagName,
        id: element.id,
        className: typeof element.className === 'string' ? element.className : '',
        right: Math.round(element.getBoundingClientRect().right),
        width: Math.round(element.getBoundingClientRect().width),
      }))
      .filter(item => item.right > window.innerWidth + 1)
      .sort((left, right) => right.right - left.right)
      .slice(0, 8),
  }))
  if (layout.documentWidth > layout.viewportWidth + 1) {
    throw new Error(`${name}: horizontal overflow ${layout.documentWidth} > ${layout.viewportWidth}: ${JSON.stringify(layout.overflowElements)}`)
  }
  if (layout.taskCount < 1 || layout.formWidth <= 0) {
    throw new Error(`${name}: annotation content did not render correctly`)
  }
  await page.screenshot({ path: output, fullPage: true })
  console.log(`${name}: tasks=${layout.taskCount} width=${layout.formWidth.toFixed(0)} screenshot=${output}`)
  await context.close()
}

await checkViewport('desktop', { width: 1440, height: 1000 }, '/tmp/reviewx-annotation-desktop.png')
await checkViewport('mobile', { width: 390, height: 844 }, '/tmp/reviewx-annotation-mobile.png')
await browser.close()
