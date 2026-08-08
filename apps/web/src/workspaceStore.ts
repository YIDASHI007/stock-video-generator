import type {PipelinePolicy} from "./api";

export type WorkflowDefinition = {
  id: string;
  name: string;
  description: string;
  contentType: string;
  enabled: boolean;
  createdAt: string;
  updatedAt: string;
  lastRunAt: string | null;
  policy: PipelinePolicy;
};

export type ContentAssetMeta = {
  outputId: string;
  title: string;
  description: string;
  topics: string[];
  state: "ready" | "favorite" | "archived" | "discarded";
  coverVariant: "portrait" | "landscape";
  updatedAt: string;
};

export type AccountPublishingRule = {
  accountId: string;
  preferredTime: string;
  minIntervalMinutes: number;
  randomDelayMinutes: number;
  dailyLimit: number;
};

const WORKFLOW_KEY = "content-ops.workflows.v1";
const CONTENT_META_KEY = "content-ops.content-meta.v1";
const ACCOUNT_RULE_KEY = "content-ops.account-rules.v1";

const read = <T>(key: string, fallback: T): T => {
  try {
    const value = localStorage.getItem(key);
    return value ? JSON.parse(value) as T : fallback;
  } catch {
    return fallback;
  }
};

const write = <T>(key: string, value: T): T => {
  localStorage.setItem(key, JSON.stringify(value));
  window.dispatchEvent(new CustomEvent("content-ops-store", {detail: key}));
  return value;
};

export const workflowStore = {
  list: (): WorkflowDefinition[] => read<WorkflowDefinition[]>(WORKFLOW_KEY, []),
  save: (items: WorkflowDefinition[]): WorkflowDefinition[] => write(WORKFLOW_KEY, items),
  upsert: (item: WorkflowDefinition): WorkflowDefinition[] => {
    const items = workflowStore.list();
    const index = items.findIndex((entry) => entry.id === item.id);
    if (index >= 0) items[index] = item;
    else items.unshift(item);
    return workflowStore.save(items);
  },
  remove: (id: string): WorkflowDefinition[] => workflowStore.save(workflowStore.list().filter((item) => item.id !== id)),
};

export const contentMetaStore = {
  all: (): Record<string, ContentAssetMeta> => read<Record<string, ContentAssetMeta>>(CONTENT_META_KEY, {}),
  get: (outputId: string): ContentAssetMeta | null => contentMetaStore.all()[outputId] ?? null,
  upsert: (item: ContentAssetMeta): ContentAssetMeta => {
    const items = contentMetaStore.all();
    items[item.outputId] = item;
    write(CONTENT_META_KEY, items);
    return item;
  },
};

export const accountRuleStore = {
  all: (): Record<string, AccountPublishingRule> => read<Record<string, AccountPublishingRule>>(ACCOUNT_RULE_KEY, {}),
  get: (accountId: string): AccountPublishingRule => accountRuleStore.all()[accountId] ?? {
    accountId,
    preferredTime: "19:30",
    minIntervalMinutes: 30,
    randomDelayMinutes: 8,
    dailyLimit: 5,
  },
  upsert: (item: AccountPublishingRule): AccountPublishingRule => {
    const items = accountRuleStore.all();
    items[item.accountId] = item;
    write(ACCOUNT_RULE_KEY, items);
    return item;
  },
};

export const makeId = (prefix: string): string => `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
