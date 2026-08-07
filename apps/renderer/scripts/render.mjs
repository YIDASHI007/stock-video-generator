import {bundle} from "@remotion/bundler";
import {
  getVideoMetadata,
  renderMedia,
  renderStill,
  selectComposition,
} from "@remotion/renderer";
import {existsSync} from "node:fs";
import {
  copyFile,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import {fileURLToPath} from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const rendererRoot = path.resolve(scriptDirectory, "..");
const chromeExecutableCandidates = [
  process.env.REMOTION_BROWSER_EXECUTABLE,
  process.env.ProgramFiles
    ? path.join(
        process.env.ProgramFiles,
        "Google",
        "Chrome",
        "Application",
        "chrome.exe",
      )
    : null,
  process.env["ProgramFiles(x86)"]
    ? path.join(
        process.env["ProgramFiles(x86)"],
        "Google",
        "Chrome",
        "Application",
        "chrome.exe",
      )
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
const browserExecutable =
  chromeExecutableCandidates.find((candidate) => existsSync(candidate)) ?? null;
const browserOptions = browserExecutable ? {browserExecutable} : {};

const cleanupStaleRenderTemps = async (root) => {
  const cutoff = Date.now() - 24 * 60 * 60 * 1000;
  const entries = await readdir(root, {withFileTypes: true});
  for (const entry of entries) {
    if (!entry.isDirectory() || !entry.name.startsWith("job-")) {
      continue;
    }
    const candidate = path.join(root, entry.name);
    try {
      const info = await stat(candidate);
      if (info.mtimeMs < cutoff) {
        await rm(candidate, {recursive: true, force: true, maxRetries: 3});
      }
    } catch {
      // Another render or cleanup pass may have removed it already.
    }
  }
};

const argument = (name) => {
  const index = process.argv.indexOf(name);
  return index === -1 ? null : process.argv[index + 1] ?? null;
};

const pngDimensions = async (filePath) => {
  const bytes = await readFile(filePath);
  const signature = bytes.subarray(0, 8);
  if (
    bytes.length < 24 ||
    !signature.equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))
  ) {
    throw new Error(`封面不是有效 PNG：${filePath}`);
  }
  return {
    width: bytes.readUInt32BE(16),
    height: bytes.readUInt32BE(20),
  };
};

const specPath = argument("--spec");
const outputPath = argument("--output");
const concurrency = Number(argument("--concurrency") ?? "1");
const compositionId =
  argument("--composition") ?? "StockHistoricalSimulationV1";
if (!specPath || !outputPath) {
  throw new Error(
    "用法：pnpm render -- --spec <visualization_spec.json> --output <output.mp4> [--composition <id>] [--concurrency <n>]",
  );
}

const absoluteSpecPath = path.resolve(specPath);
const absoluteOutputPath = path.resolve(outputPath);
const outputParts = path.parse(absoluteOutputPath);
const renderTempRoot = path.join(outputParts.dir, ".render-temp");
await mkdir(renderTempRoot, {recursive: true});
await cleanupStaleRenderTemps(renderTempRoot);
const renderTempDir = await mkdtemp(path.join(renderTempRoot, "job-"));
process.env.TEMP = renderTempDir;
process.env.TMP = renderTempDir;
const portraitCoverPath = path.join(
  outputParts.dir,
  `${outputParts.name}.cover-portrait.png`,
);
const landscapeCoverPath = path.join(
  outputParts.dir,
  `${outputParts.name}.cover-landscape.png`,
);
const spec = JSON.parse(await readFile(absoluteSpecPath, "utf8"));
if (!Array.isArray(spec.series) || spec.series.length < 2) {
  throw new Error("visualization_spec.series 至少需要两个真实数据点。");
}

// 配音音频：复制到 renderer 的 public 目录供 staticFile() 引用，渲染结束（无论成败）清理。
const narrationClips = spec.narration?.audio ?? [];
const narrationPublicDir = narrationClips.length
  ? path.join(rendererRoot, "public", "narration", spec.simulation_id)
  : null;
if (narrationPublicDir) {
  await mkdir(narrationPublicDir, {recursive: true});
  for (const clip of narrationClips) {
    const target = path.join(narrationPublicDir, path.basename(clip.file));
    await copyFile(clip.source_path, target);
  }
}

// 背景音乐：同样复制到 public 目录，渲染结束清理。
const bgm = spec.bgm ?? null;
const bgmPublicDir = bgm
  ? path.join(rendererRoot, "public", "bgm", spec.simulation_id)
  : null;
if (bgmPublicDir) {
  await mkdir(bgmPublicDir, {recursive: true});
  await copyFile(bgm.source_path, path.join(bgmPublicDir, path.basename(bgm.file)));
}

try {
  console.log(JSON.stringify({stage: "BUNDLING", progress: 0.02}));
  const serveUrl = await bundle({
    entryPoint: path.join(rendererRoot, "src", "index.ts"),
    outDir: path.join(renderTempDir, "bundle"),
  });
  const inputProps = {spec};
  const composition = await selectComposition({
    serveUrl,
    id: compositionId,
    inputProps,
    ...browserOptions,
  });

  let lastReported = -1;
  await renderMedia({
    composition,
    serveUrl,
    codec: "h264",
    outputLocation: absoluteOutputPath,
    inputProps,
    concurrency,
    crf: 18,
    imageFormat: "jpeg",
    jpegQuality: 92,
    pixelFormat: "yuv420p",
    colorSpace: "bt709",
    x264Preset: "medium",
    overwrite: true,
    ...browserOptions,
    onProgress: ({progress}) => {
      const rounded = Math.floor(progress * 100);
      if (rounded >= lastReported + 2 || rounded === 100) {
        lastReported = rounded;
        console.log(
          JSON.stringify({
            stage: "RENDERING_VIDEO",
            progress: 0.05 + progress * 0.9,
          }),
        );
      }
    },
  });

  console.log(JSON.stringify({stage: "RENDERING_COVERS", progress: 0.95}));
  const portraitComposition = await selectComposition({
    serveUrl,
    id: "StockCoverPortrait",
    inputProps,
    ...browserOptions,
  });
  await renderStill({
    composition: portraitComposition,
    serveUrl,
    output: portraitCoverPath,
    inputProps,
    imageFormat: "png",
    overwrite: true,
    ...browserOptions,
  });
  const landscapeComposition = await selectComposition({
    serveUrl,
    id: "StockCoverLandscape",
    inputProps,
    ...browserOptions,
  });
  await renderStill({
    composition: landscapeComposition,
    serveUrl,
    output: landscapeCoverPath,
    inputProps,
    imageFormat: "png",
    overwrite: true,
    ...browserOptions,
  });

  console.log(JSON.stringify({stage: "VALIDATING_OUTPUT", progress: 0.96}));
  const metadata = await getVideoMetadata(absoluteOutputPath);
  const file = await stat(absoluteOutputPath);
  const portraitCoverFile = await stat(portraitCoverPath);
  const landscapeCoverFile = await stat(landscapeCoverPath);
  const portraitDimensions = await pngDimensions(portraitCoverPath);
  const landscapeDimensions = await pngDimensions(landscapeCoverPath);
  const expected = spec.composition;
  const errors = [];
  if (metadata.width !== expected.width) errors.push(`宽度应为 ${expected.width}`);
  if (metadata.height !== expected.height)
    errors.push(`高度应为 ${expected.height}`);
  if (Math.abs(metadata.fps - expected.fps) > 0.01)
    errors.push(`帧率应为 ${expected.fps}`);
  if (Math.abs(metadata.durationInSeconds - expected.duration_seconds) > 0.15) {
    errors.push(`时长应为 ${expected.duration_seconds} 秒`);
  }
  if (metadata.codec !== "h264") errors.push("编码应为 H.264");
  if (metadata.pixelFormat !== "yuv420p") errors.push("像素格式应为 yuv420p");
  if (metadata.colorSpace !== "bt709") errors.push("色彩空间应为 BT.709");
  if ((narrationClips.length > 0 || bgm) && metadata.audioCodec !== "aac") {
    errors.push(`含音频的成片必须包含 AAC 音频流，实际为 ${metadata.audioCodec}`);
  }
  if (!metadata.canPlayInVideoTag) errors.push("视频无法在 HTML video 元素中播放");
  if (!metadata.supportsSeeking) errors.push("视频不支持定位播放");
  if (file.size <= 0) errors.push("输出文件为空");
  if (portraitCoverFile.size <= 0) errors.push("竖版封面文件为空");
  if (landscapeCoverFile.size <= 0) errors.push("横版封面文件为空");
  if (
    portraitDimensions.width !== 1080 ||
    portraitDimensions.height !== 1440
  ) {
    errors.push(
      `竖版封面应为 1080×1440，实际为 ${portraitDimensions.width}×${portraitDimensions.height}`,
    );
  }
  if (
    landscapeDimensions.width !== 1440 ||
    landscapeDimensions.height !== 1080
  ) {
    errors.push(
      `横版封面应为 1440×1080，实际为 ${landscapeDimensions.width}×${landscapeDimensions.height}`,
    );
  }

  const report = {
    valid: errors.length === 0,
    errors,
    output_path: absoluteOutputPath,
    file_size_bytes: file.size,
    covers: {
      portrait: {
        path: portraitCoverPath,
        width: portraitDimensions.width,
        height: portraitDimensions.height,
        file_size_bytes: portraitCoverFile.size,
      },
      landscape: {
        path: landscapeCoverPath,
        width: landscapeDimensions.width,
        height: landscapeDimensions.height,
        file_size_bytes: landscapeCoverFile.size,
      },
    },
    inspected_by: "@remotion/renderer getVideoMetadata (bundled FFprobe)",
    metadata,
    expected,
  };
  const reportPath = `${absoluteOutputPath}.validation.json`;
  await writeFile(reportPath, JSON.stringify(report, null, 2), "utf8");
  if (errors.length > 0) {
    throw new Error(`视频校验失败：${errors.join("；")}`);
  }
  console.log(
    JSON.stringify({
      stage: "COMPLETED",
      progress: 1,
      output: absoluteOutputPath,
      cover_portrait: portraitCoverPath,
      cover_landscape: landscapeCoverPath,
      validation_report: reportPath,
    }),
  );
} finally {
  if (narrationPublicDir) {
    await rm(narrationPublicDir, {recursive: true, force: true});
  }
  if (bgmPublicDir) {
    await rm(bgmPublicDir, {recursive: true, force: true});
  }
  try {
    await rm(renderTempDir, {
      recursive: true,
      force: true,
      maxRetries: 3,
      retryDelay: 200,
    });
  } catch (error) {
    console.error(
      JSON.stringify({
        stage: "CLEANUP_WARNING",
        path: renderTempDir,
        error: error instanceof Error ? error.message : String(error),
      }),
    );
  }
}
