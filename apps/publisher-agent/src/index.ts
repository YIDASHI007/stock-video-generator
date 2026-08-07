import {readFile, writeFile, mkdir} from "node:fs/promises";
import {dirname, resolve} from "node:path";
import process from "node:process";
import {Stagehand} from "@browserbasehq/stagehand";
import {z} from "zod";

const RequestSchema = z.object({
  schema_version: z.literal("1.0"),
  goal: z.string().min(1).max(1000),
  stage: z.string().min(1),
  cdp_url: z.string().url(),
  screenshot_path: z.string().min(1),
  result_path: z.string().min(1),
  max_steps: z.number().int().min(1).max(4),
  forbidden_actions: z.array(z.string().min(1)).min(1),
});

type AgentRequest = z.infer<typeof RequestSchema>;

type AgentResult = {
  success: boolean;
  message: string;
  stage: string;
  action_description?: string;
  actions?: unknown[];
  error_type?: string;
};

async function persistResult(request: AgentRequest, result: AgentResult): Promise<void> {
  const resultPath = resolve(request.result_path);
  await mkdir(dirname(resultPath), {recursive: true});
  await writeFile(resultPath, JSON.stringify(result, null, 2), "utf8");
}

async function main(): Promise<void> {
  const requestFile = process.argv[2];
  if (!requestFile) {
    throw new Error("Usage: node dist/index.js <agent-request.json>");
  }
  const request = RequestSchema.parse(
    JSON.parse(await readFile(resolve(requestFile), "utf8")),
  );
  const forbidden = request.forbidden_actions.map((item) => `- ${item}`).join("\n");
  const model = process.env.PUBLISH_AGENT_MODEL ?? "openai/gpt-4.1-mini";
  const cacheDir = resolve(dirname(request.result_path), "stagehand-cache");
  const stagehand = new Stagehand({
    env: "LOCAL",
    localBrowserLaunchOptions: {
      cdpUrl: request.cdp_url,
      connectTimeoutMs: 15_000,
    },
    model,
    selfHeal: true,
    disablePino: true,
    verbose: 0,
    actTimeoutMs: 45_000,
    cacheDir,
    systemPrompt: [
      "你是抖音创作者中心发布流程中的受限界面恢复助手。",
      "只完成调用方给出的当前局部目标，最多执行一个 Stagehand act。",
      "不得主动发布、不得处理验证码、不得绕过登录或安全校验。",
      "不得更改标题、简介、话题、视频、封面文件或任何业务事实。",
      "遇到需要登录、短信、人机验证或无法确定的界面时立即失败并交回人工。",
      "明确禁止的动作：",
      forbidden,
    ].join("\n"),
  });

  try {
    await stagehand.init();
    const result = await stagehand.act(request.goal, {timeout: 45_000});
    await persistResult(request, {
      success: result.success,
      message: result.message,
      stage: request.stage,
      action_description: result.actionDescription,
      actions: result.actions,
    });
    if (!result.success) {
      process.exitCode = 2;
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    await persistResult(request, {
      success: false,
      message,
      stage: request.stage,
      error_type: error instanceof Error ? error.name : "UnknownError",
    });
    process.exitCode = 1;
  } finally {
    await stagehand.close().catch(() => undefined);
  }
}

await main();
