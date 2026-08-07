import {bundle} from "@remotion/bundler";
import {renderStill, selectComposition} from "@remotion/renderer";
import {existsSync} from "node:fs";
import {mkdir, readFile} from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import {fileURLToPath} from "node:url";

const argument = (name) => {
  const index = process.argv.indexOf(name);
  return index === -1 ? null : process.argv[index + 1] ?? null;
};

const specPath = argument("--spec");
const outputDir = argument("--output-dir");
const compositionId = argument("--composition") ?? "StoryNarrativePrototype";
const frames = (argument("--frames") ?? "15,900,1710")
  .split(",")
  .map(Number)
  .filter(Number.isFinite);

if (!specPath || !outputDir) {
  throw new Error("需要 --spec 与 --output-dir。");
}

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const rendererRoot = path.resolve(scriptDirectory, "..");
const browserCandidates = [
  process.env.REMOTION_BROWSER_EXECUTABLE,
  process.env.ProgramFiles
    ? path.join(process.env.ProgramFiles, "Google", "Chrome", "Application", "chrome.exe")
    : null,
  process.env.LOCALAPPDATA
    ? path.join(process.env.LOCALAPPDATA, "Google", "Chrome", "Application", "chrome.exe")
    : null,
].filter(Boolean);
const browserExecutable = browserCandidates.find((candidate) => existsSync(candidate));
const browserOptions = browserExecutable ? {browserExecutable} : {};

const spec = JSON.parse(await readFile(path.resolve(specPath), "utf8"));
// Audio has no visual effect and does not need to be staged for QA stills.
delete spec.bgm;
await mkdir(path.resolve(outputDir), {recursive: true});
const serveUrl = await bundle({
  entryPoint: path.join(rendererRoot, "src", "index.ts"),
});
const inputProps = {spec};
const composition = await selectComposition({
  serveUrl,
  id: compositionId,
  inputProps,
  ...browserOptions,
});

for (const frame of frames) {
  await renderStill({
    composition,
    serveUrl,
    inputProps,
    frame,
    output: path.join(path.resolve(outputDir), `frame-${frame}.png`),
    imageFormat: "png",
    overwrite: true,
    ...browserOptions,
  });
}
