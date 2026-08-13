import {readdir, readFile} from "node:fs/promises";
import {extname, join, relative} from "node:path";
import {fileURLToPath} from "node:url";

const webRoot = fileURLToPath(new URL("..", import.meta.url));
const sourceRoot = join(webRoot, "src");
const forbiddenNoticeClass = /className\s*=\s*["'`][^"'`]*\b(?:ok|info|warning|error)-notice\b/;
const violations = [];

async function inspect(directory) {
  for (const entry of await readdir(directory, {withFileTypes: true})) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      await inspect(path);
      continue;
    }
    if (![".tsx", ".jsx"].includes(extname(entry.name))) continue;

    const source = await readFile(path, "utf8");
    if (forbiddenNoticeClass.test(source)) {
      violations.push(relative(webRoot, path));
    }
  }
}

await inspect(sourceRoot);

if (violations.length) {
  console.error("Design-system check failed: use Notice/SuccessNotice/InfoNotice/WarningNotice/ErrorNotice instead of legacy notice classes.");
  for (const path of violations) console.error(`- ${path}`);
  process.exitCode = 1;
} else {
  console.log("Design-system check passed.");
}
