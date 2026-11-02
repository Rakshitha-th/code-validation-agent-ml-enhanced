const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { chromium } = require("playwright-core");

const [sourceRootArg, compiledRootArg, entryArg, htmlArg] = process.argv.slice(2);
const sourceRoot = path.resolve(sourceRootArg);
const compiledRoot = compiledRootArg ? path.resolve(compiledRootArg) : null;
const entry = entryArg.split(path.sep).join("/");
const htmlPath = htmlArg ? path.resolve(sourceRoot, htmlArg) : null;
const htmlUrlPath = htmlPath
  ? `/${path.relative(sourceRoot, htmlPath).split(path.sep).join("/")}`
  : null;
const htmlReferencesEntry = htmlPath
  ? new RegExp(path.basename(entryArg).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).test(
      fs.readFileSync(htmlPath, "utf8"),
    )
  : false;
const consoleOutput = [];
const runtimeErrors = [];
const missingFiles = [];

function resolveFile(root, relativePath) {
  const resolved = path.resolve(root, relativePath);
  return resolved === root || resolved.startsWith(`${root}${path.sep}`)
    ? resolved
    : null;
}

function sendFile(response, root, relativePath) {
  const filePath = resolveFile(root, relativePath);
  if (!filePath || !fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
    missingFiles.push(relativePath);
    response.writeHead(404);
    response.end("Not found");
    return;
  }
  response.writeHead(200, {
    "Content-Type": filePath.endsWith(".html")
      ? "text/html; charset=utf-8"
      : filePath.endsWith(".js")
        ? "text/javascript; charset=utf-8"
        : "application/octet-stream",
  });
  fs.createReadStream(filePath).pipe(response);
}

function collectElementIds(root, ids) {
  if (!root || !fs.existsSync(root)) {
    return;
  }
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const filePath = path.join(root, entry.name);
    if (entry.isDirectory()) {
      collectElementIds(filePath, ids);
      continue;
    }
    if (!/\.(?:js|jsx|ts|tsx)$/i.test(entry.name)) {
      continue;
    }
    const source = fs.readFileSync(filePath, "utf8");
    const patterns = [
      /getElementById\(\s*["']([^"']+)["']\s*\)/g,
      /querySelector(?:All)?\(\s*["']#([A-Za-z_][\w-]*)["']\s*\)/g,
    ];
    for (const pattern of patterns) {
      for (const match of source.matchAll(pattern)) {
        ids.add(match[1]);
      }
    }
  }
}

function generatedDomHarness(root) {
  const elementIds = new Set();
  collectElementIds(root, elementIds);
  const elements = [...elementIds]
    .map((id) => `<div id="${id.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;")}"></div>`)
    .join("");
  return `<!doctype html><html><head><meta charset="utf-8"></head><body>${elements}</body></html>`;
}

async function main() {
  const generatedHtml = htmlPath ? null : generatedDomHarness(sourceRoot);
  const server = http.createServer((request, response) => {
    const requestPath = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
    if (htmlPath && requestPath === htmlUrlPath) {
      sendFile(response, sourceRoot, path.relative(sourceRoot, htmlPath));
    } else if (requestPath === "/favicon.ico") {
      response.writeHead(204);
      response.end();
    } else if (requestPath === "/__generated_dom.html" && generatedHtml) {
      response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      response.end(generatedHtml);
    } else if (requestPath.startsWith("/__compiled/") && compiledRoot) {
      sendFile(response, compiledRoot, requestPath.slice("/__compiled/".length));
    } else if (requestPath.startsWith("/__source/")) {
      sendFile(response, sourceRoot, requestPath.slice("/__source/".length));
    } else {
      sendFile(response, sourceRoot, requestPath.slice(1));
    }
  });

  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  const baseUrl = `http://127.0.0.1:${address.port}`;
  let browser;
  try {
    browser = await chromium.launch({
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || "/usr/bin/chromium",
      headless: true,
      args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
    });
    const page = await browser.newPage();
    page.on("pageerror", (error) => runtimeErrors.push(error.message));
    page.on("requestfailed", (request) => {
      const failure = request.failure();
      runtimeErrors.push(`Request failed: ${failure?.errorText || "unknown network error"}`);
    });
    page.on("console", (message) => {
      if (message.type() === "error") {
        runtimeErrors.push(`console.error: ${message.text()}`);
      } else if (["log", "info", "warn"].includes(message.type())) {
        consoleOutput.push(message.text());
      }
    });

    if (htmlPath) {
      await page.goto(`${baseUrl}${htmlUrlPath}`, { waitUntil: "load", timeout: 20000 });
    } else {
      await page.goto(`${baseUrl}/__generated_dom.html`, {
        waitUntil: "load",
        timeout: 20000,
      });
    }

    if (!htmlPath || compiledRoot || !htmlReferencesEntry) {
      const scriptPath = compiledRoot
        ? `/__compiled/${entry}`
        : `/__source/${entry}`;
      try {
        await page.addScriptTag({ url: `${baseUrl}${scriptPath}`, type: "module" });
      } catch (error) {
        const missing = missingFiles.length
          ? ` Missing browser files: ${missingFiles.join(", ")}.`
          : "";
        const requestErrors = runtimeErrors
          .filter((message) => message.startsWith("Request failed:"))
          .join("; ");
        const requestFailure = requestErrors ? ` ${requestErrors}` : "";
        throw new Error(`${error.message}${missing}${requestFailure}`);
      }
    }
    await page.waitForTimeout(100);

    const output = consoleOutput.length
      ? consoleOutput.join("\n")
      : (await page.locator("body").innerText()).trim();
    if (output) {
      process.stdout.write(`${output}\n`);
    }
    if (runtimeErrors.length) {
      process.stderr.write(`${runtimeErrors.join("\n")}\n`);
      process.exitCode = 1;
    }
  } finally {
    if (browser) {
      await browser.close();
    }
    server.close();
  }
}

main().catch((error) => {
  process.stderr.write(`Browser execution failed: ${error.message}\n`);
  process.exitCode = 1;
});
