import {Player} from "@remotion/player";
import {ScrollingStockVideo} from "@stock-video/video-template";
import React, {useCallback, useEffect, useState} from "react";
import {Link, useParams} from "react-router-dom";

import {
  api,
  coverUrl,
  type Job,
  type Output,
  type VisualizationSpec,
  videoUrl,
} from "../api";
import {BackLink, ErrorNotice, PageHeader} from "../components";

export const PreviewPage: React.FC = () => {
  const {simulationId = ""} = useParams();
  const [spec, setSpec] = useState<VisualizationSpec | null>(null);
  const [renderJob, setRenderJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [output, setOutput] = useState<Output | null>(null);

  const load = useCallback(async () => {
    try {
      const value = await api<VisualizationSpec>(
        `/api/simulations/${simulationId}/visualization-spec`,
      );
      setSpec(value);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [simulationId]);

  useEffect(() => {
    void load();
    void api<Output[]>(`/api/outputs?simulation_id=${simulationId}`)
      .then((outputs) => setOutput(outputs[0] ?? null))
      .catch(() => undefined);
  }, [load]);

  useEffect(() => {
    if (!renderJob || ["COMPLETED", "FAILED_FINAL", "CANCELLED"].includes(renderJob.stage)) {
      return;
    }
    const timer = window.setInterval(async () => {
      try {
        const current = await api<Job>(`/api/jobs/${renderJob.job_id}`);
        setRenderJob(current);
        if (current.stage === "COMPLETED") {
          const outputs = await api<Output[]>(
            `/api/outputs?simulation_id=${simulationId}`,
          );
          setOutput(outputs[0] ?? null);
        }
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [renderJob?.job_id, renderJob?.stage, simulationId]);

  const saveSpec = async (): Promise<VisualizationSpec | null> => {
    if (!spec) return null;
    setSaving(true);
    try {
      const saved = await api<VisualizationSpec>(
        `/api/simulations/${simulationId}/visualization-spec`,
        {method: "PUT", body: JSON.stringify(spec)},
      );
      setSpec(saved);
      return saved;
    } finally {
      setSaving(false);
    }
  };

  const render = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await saveSpec();
      const job = await api<Job>("/api/renders", {
        method: "POST",
        body: JSON.stringify({simulation_id: simulationId, priority: 100}),
      });
      setRenderJob(job);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="page">
      <BackLink to={`/simulations/${simulationId}`} label="返回回测详情" />
      <PageHeader
        eyebrow="REMOTION PREVIEW"
        title="视频预览与渲染"
        description="播放器和最终 MP4 使用同一个 visualization_spec；点击渲染后由后端逐帧生成，不进行浏览器录屏。"
      />
      {error ? <ErrorNotice message={error} /> : null}
      {spec ? (
        <div className="preview-layout">
          <div className="video-stage">
            <Player
              component={ScrollingStockVideo}
              inputProps={{spec}}
              durationInFrames={
                spec.composition.duration_seconds * spec.composition.fps
              }
              compositionWidth={spec.composition.width}
              compositionHeight={spec.composition.height}
              fps={spec.composition.fps}
              controls
              loop
              style={{
                width: "100%",
                aspectRatio: `${spec.composition.width} / ${spec.composition.height}`,
              }}
            />
          </div>
          <aside className="preview-sidebar panel">
            <div className="panel-heading">
              <h2>渲染参数</h2>
            </div>
            <dl className="detail-list">
              <div>
                <dt>模板版本</dt>
                <dd>{spec.template_version.toUpperCase()}</dd>
              </div>
              <div>
                <dt>画布</dt>
                <dd>
                  {spec.composition.width} × {spec.composition.height}
                </dd>
              </div>
              <div>
                <dt>帧率</dt>
                <dd>{spec.composition.fps} FPS</dd>
              </div>
              <div>
                <dt>时长</dt>
                <dd>{spec.composition.duration_seconds} 秒</dd>
              </div>
              <div>
                <dt>数据点</dt>
                <dd>{spec.series.length}</dd>
              </div>
              <div>
                <dt>关键节点</dt>
                <dd>{spec.milestones.length}</dd>
              </div>
              <div>
                <dt>编码</dt>
                <dd>H.264 · yuv420p · BT.709</dd>
              </div>
            </dl>
            <div className="spec-editor">
              <label>
                标题
                <input
                  value={spec.title}
                  onChange={(event) =>
                    setSpec({...spec, title: event.target.value})
                  }
                />
              </label>
              <label>
                时长
                <select
                  value={spec.composition.duration_seconds}
                  onChange={(event) => {
                    const duration = Number(event.target.value);
                    setSpec({
                      ...spec,
                      composition: {...spec.composition, duration_seconds: duration},
                      timeline: {
                        intro_seconds: 2,
                        chart_seconds: Math.max(9, duration - 6),
                        outro_seconds: 4,
                      },
                    });
                  }}
                >
                  {[15, 30, 45, 60].map((duration) => (
                    <option key={duration} value={duration}>
                      {duration} 秒
                    </option>
                  ))}
                </select>
              </label>
              <label>
                上涨色
                <input
                  type="color"
                  value={spec.chart.line_color_positive}
                  onChange={(event) =>
                    setSpec({
                      ...spec,
                      chart: {
                        ...spec.chart,
                        line_color_positive: event.target.value,
                      },
                    })
                  }
                />
              </label>
              <label>
                下跌色
                <input
                  type="color"
                  value={spec.chart.line_color_negative}
                  onChange={(event) =>
                    setSpec({
                      ...spec,
                      chart: {
                        ...spec.chart,
                        line_color_negative: event.target.value,
                      },
                    })
                  }
                />
              </label>
              <button
                className="button secondary wide"
                onClick={() => void saveSpec()}
                disabled={saving || submitting}
              >
                {saving ? "正在保存…" : "保存视频配置"}
              </button>
            </div>
            <button
              className="button primary wide"
              onClick={render}
              disabled={submitting || Boolean(renderJob)}
            >
              {submitting ? "正在创建渲染任务…" : "生成视频与双封面"}
            </button>
            {renderJob ? (
              <div className="render-created">
                渲染状态：{renderJob.stage} ·{" "}
                {Math.round(renderJob.progress * 100)}%
                <Link to={`/jobs?focus=${renderJob.job_id}`}>查看实时进度</Link>
              </div>
            ) : null}
            <p className="fine-print">{spec.disclaimer}</p>
          </aside>
        </div>
      ) : null}
      {output ? (
        <section className="panel preview-output">
          <div className="panel-heading">
            <h2>最终视频与封面</h2>
            <span>{output.video_path}</span>
          </div>
          <div className="preview-output-grid">
            <div>
              <h3>视频</h3>
              <video src={videoUrl(output.output_id)} controls preload="metadata" />
            </div>
            {output.cover_landscape_path ? (
              <figure className="cover-result landscape">
                <figcaption>横版封面 · 4:3</figcaption>
                <img
                  src={coverUrl(output.output_id, "landscape")}
                  alt="横版视频封面"
                />
              </figure>
            ) : null}
            {output.cover_portrait_path ? (
              <figure className="cover-result portrait">
                <figcaption>竖版封面 · 3:4</figcaption>
                <img
                  src={coverUrl(output.output_id, "portrait")}
                  alt="竖版视频封面"
                />
              </figure>
            ) : null}
          </div>
          <div className="job-actions">
            <a href={videoUrl(output.output_id)} download>下载 MP4</a>
            {output.cover_landscape_path ? (
              <a href={coverUrl(output.output_id, "landscape")} download>
                下载横版封面
              </a>
            ) : null}
            {output.cover_portrait_path ? (
              <a href={coverUrl(output.output_id, "portrait")} download>
                下载竖版封面
              </a>
            ) : null}
            <button
              type="button"
              onClick={() =>
                void api(`/api/outputs/${output.output_id}/open-folder`, {
                  method: "POST",
                }).catch((reason) =>
                  setError(reason instanceof Error ? reason.message : String(reason)),
                )
              }
            >
              在资源管理器中打开
            </button>
          </div>
        </section>
      ) : null}
    </div>
  );
};
