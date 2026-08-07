import {bundle} from "@remotion/bundler";
import {renderStill, selectComposition} from "@remotion/renderer";
import {existsSync} from "node:fs";
import {mkdir, mkdtemp, readFile, rm} from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import {fileURLToPath} from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const rendererRoot = path.resolve(scriptDirectory, "..");

const argument = (name) => {
  const index = process.argv.indexOf(name);
  return index === -1 ? null : process.argv[index + 1] ?? null;
};

const specPath = argument("--spec");
const outputPath = argument("--output");
const frame = Number(argument("--frame") ?? "0");
const compositionId = argument("--composition") ?? "StockFilledAreaPrototype";
if (!specPath || !outputPath || !Number.isFinite(frame) || frame < 0) {
  throw new Error(
    "用法：node render-still.mjs --spec <spec.json> --output <frame.png> --frame <n> [--composition <id>]",
  );
}

const chromeCandidates = [
  process.env.REMOTION_BROWSER_EXECUTABLE,
  process.env.ProgramFiles
    ? path.join(process.env.ProgramFiles, "Google", "Chrome", "Application", "chrome.exe")
    : null,
  process.env.LOCALAPPDATA
    ? path.join(
        process.env.LOCALAPPDATA,
        "Google",
        "Chrome",
        "Application",
        "chrome.exe",
      )
    : null,
].filter(Boolean);
const browserExecutable = chromeCandidates.find((candidate) => existsSync(candidate));
const browserOptions = browserExecutable ? {browserExecutable} : {};

const absoluteOutput = path.resolve(outputPath);
const tempRoot = path.join(path.dirname(absoluteOutput), ".render-temp");
await mkdir(tempRoot, {recursive: true});
const tempDir = await mkdtemp(path.join(tempRoot, "still-"));

try {
  const spec = JSON.parse(await readFile(path.resolve(specPath), "utf8"));
  const serveUrl = await bundle({
    entryPoint: path.join(rendererRoot, "src", "index.ts"),
    outDir: path.join(tempDir, "bundle"),
  });
  const inputProps = {spec};
  const composition = await selectComposition({
    serveUrl,
    id: compositionId,
    inputProps,
    ...browserOptions,
  });
  await renderStill({
    composition,
    serveUrl,
    output: absoluteOutput,
    inputProps,
    frame: Math.min(Math.round(frame), composition.durationInFrames - 1),
    imageFormat: "png",
    overwrite: true,
    ...browserOptions,
  });
  console.log(absoluteOutput);
} finally {
  await rm(tempDir, {recursive: true, force: true, maxRetries: 3});
}
