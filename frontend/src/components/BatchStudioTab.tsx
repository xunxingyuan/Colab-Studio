import { useState } from "react";
import { CheckCircle2, Clapperboard, Film, Layers, Play, Subtitles, Wand2 } from "lucide-react";
import { api, type BatchJob, type HarvestArtifact, type RuntimeSession } from "../lib/api";

interface Props {
  sessions: RuntimeSession[];
  batchJobs: BatchJob[];
  artifacts: HarvestArtifact[];
  busy: string | null;
  runAction: (label: string, fn: () => Promise<void>) => Promise<void>;
  notify: (kind: "ok" | "err", text: string) => void;
  onGoOutputs: () => void;
}

const DEFAULT_SCRIPT = `Dark stormy boardroom, chairman slams resignation paper on table | 董事会拍桌放狠话：公司离了你照样转！
Wall Street trading floor crashing in bright red numbers | 仅仅十一天，全球股市崩盘蒸发两万亿！
Man in black leather jacket calmly eating noodles, phone showing 99 missed calls | 深夜小馆淡定吃面，手机弹出 99 个未接来电
Wall Street screens turn emerald green +31%, traders cheering | 王者归来！开盘暴涨 31%，全华尔街沸腾！`;

export default function BatchStudioTab({
  sessions,
  batchJobs,
  artifacts,
  busy,
  runAction,
  notify,
  onGoOutputs,
}: Props) {
  const [title, setTitle] = useState("皮衣归来_测试短剧");
  const [sessionName, setSessionName] = useState(sessions[0]?.name || "");
  const [workflowMode, setWorkflowMode] = useState<"wan21_builtin" | "custom_json">("wan21_builtin");
  const [customJson, setCustomJson] = useState("");
  const [scriptText, setScriptText] = useState(DEFAULT_SCRIPT);
  const [width, setWidth] = useState(640);
  const [height, setHeight] = useState(384);
  const [frames, setFrames] = useState(33);
  const [steps, setSteps] = useState(20);
  const [autoStitch, setAutoStitch] = useState(true);
  const [burnSubtitles, setBurnSubtitles] = useState(true);
  const [autoStop, setAutoStop] = useState(false);

  // Standalone composer state
  const videoArtifacts = artifacts.filter((a) => a.media_type === "video");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [composeTitle, setComposeTitle] = useState("中文字幕精修成片");
  const [composeSubs, setComposeSubs] = useState<Record<string, string>>({});
  const [composeBurn, setComposeBurn] = useState(true);

  const effectiveSession = sessionName || sessions[0]?.name || "";

  const handleStartBatch = () => {
    if (!effectiveSession) {
      notify("err", "请先在「算力总览」接管或启动一台运行中的 GPU 实例。");
      return;
    }
    const shots = scriptText
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line, idx) => {
        const [prompt, subtitle = ""] = line.split("|").map((x) => x.trim());
        return { prompt, subtitle, seed: 42 + idx };
      });
    if (shots.length === 0) {
      notify("err", "请至少输入 1 条分镜提示词。");
      return;
    }
    runAction("batch", async () => {
      await api.createBatch({
        title,
        session_name: effectiveSession,
        shots,
        workflow_mode: workflowMode,
        custom_workflow_json: workflowMode === "custom_json" ? customJson : undefined,
        width,
        height,
        frames,
        steps,
        auto_stitch: autoStitch,
        burn_subtitles: burnSubtitles,
        auto_stop: autoStop,
      });
      notify("ok", `已提交 ${shots.length} 镜批量 ComfyUI 视频队列`);
    });
  };

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const handleCompose = () => {
    if (selectedIds.length === 0) {
      notify("err", "请至少勾选 1 个视频成品。");
      return;
    }
    runAction("compose", async () => {
      const subs = selectedIds.map((id) => composeSubs[id] || "");
      await api.compose({
        artifact_ids: selectedIds,
        subtitles: subs,
        title: composeTitle,
        burn_subtitles: composeBurn,
      });
      notify("ok", "多镜拼接与中文字幕压制已完成！已加入成品库。");
      onGoOutputs();
    });
  };

  return (
    <div className="grid gap-6 lg:grid-cols-12">
      {/* Left: Batch ComfyUI Queue */}
      <div className="space-y-5 lg:col-span-7">
        <div className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center justify-between">
            <h2 className="flex items-center gap-2 font-semibold">
              <Layers className="h-4 w-4 text-emerald-400" />
              批量 ComfyUI 视频工作流队列
            </h2>
            <span className="text-xs text-zinc-500">格式：画面提示词 | 中文字幕台词</span>
          </div>

          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs text-zinc-400">任务名称</label>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-zinc-400">目标运行中实例</label>
              <select
                value={effectiveSession}
                onChange={(e) => setSessionName(e.target.value)}
                className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
              >
                {sessions.length === 0 && <option value="">（当前无运行中 GPU 实例）</option>}
                {sessions.map((s) => (
                  <option key={s.endpoint} value={s.name}>
                    {s.name} ({s.accelerator})
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="mt-3 flex gap-2">
            <button
              onClick={() => setWorkflowMode("wan21_builtin")}
              className={`rounded-lg px-3 py-1.5 text-xs font-medium ${
                workflowMode === "wan21_builtin"
                  ? "bg-emerald-600 text-white"
                  : "border border-zinc-700 bg-zinc-950 text-zinc-400"
              }`}
            >
              内置 Wan2.1-T2V-1.3B 模板
            </button>
            <button
              onClick={() => setWorkflowMode("custom_json")}
              className={`rounded-lg px-3 py-1.5 text-xs font-medium ${
                workflowMode === "custom_json"
                  ? "bg-emerald-600 text-white"
                  : "border border-zinc-700 bg-zinc-950 text-zinc-400"
              }`}
            >
              自定义 ComfyUI API JSON
            </button>
          </div>

          {workflowMode === "wan21_builtin" ? (
            <div className="mt-3 grid grid-cols-4 gap-2">
              {[
                { label: "宽 (px)", val: width, set: setWidth },
                { label: "高 (px)", val: height, set: setHeight },
                { label: "帧数 (4k+1)", val: frames, set: setFrames },
                { label: "采样步数", val: steps, set: setSteps },
              ].map((item) => (
                <div key={item.label}>
                  <label className="mb-1 block text-[11px] text-zinc-400">{item.label}</label>
                  <input
                    type="number"
                    value={item.val}
                    onChange={(e) => item.set(Number(e.target.value))}
                    className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-2.5 py-1.5 text-xs"
                  />
                </div>
              ))}
            </div>
          ) : (
            <div className="mt-3">
              <label className="mb-1 block text-xs text-zinc-400">
                粘贴 ComfyUI API JSON（支持占位符 {"{{PROMPT}}"}、{"{{SEED}}"}、{"{{PREFIX}}"}）
              </label>
              <textarea
                rows={4}
                value={customJson}
                onChange={(e) => setCustomJson(e.target.value)}
                placeholder='{"4": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{PROMPT}}" ...}}}'
                className="w-full rounded-lg border border-zinc-700 bg-zinc-950 p-2.5 font-mono text-xs"
              />
            </div>
          )}

          <div className="mt-3">
            <label className="mb-1 block text-xs text-zinc-400">多镜分镜脚本（每行一镜：`画面提示词 | 中文字幕`）</label>
            <textarea
              rows={5}
              value={scriptText}
              onChange={(e) => setScriptText(e.target.value)}
              className="w-full rounded-xl border border-zinc-700 bg-zinc-950 p-3 font-mono text-xs leading-relaxed"
            />
          </div>

          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap gap-4 text-xs text-zinc-300">
              <label className="flex items-center gap-1.5">
                <input
                  type="checkbox"
                  checked={autoStitch}
                  onChange={(e) => setAutoStitch(e.target.checked)}
                  className="accent-emerald-500"
                />
                自动拼接完整成片
              </label>
              <label className="flex items-center gap-1.5">
                <input
                  type="checkbox"
                  checked={burnSubtitles}
                  onChange={(e) => setBurnSubtitles(e.target.checked)}
                  className="accent-emerald-500"
                />
                自动压制中文字幕 + SRT
              </label>
              <label className="flex items-center gap-1.5 text-amber-300">
                <input
                  type="checkbox"
                  checked={autoStop}
                  onChange={(e) => setAutoStop(e.target.checked)}
                  className="accent-amber-500"
                />
                全部出片后自动关机
              </label>
            </div>
            <button
              disabled={busy !== null}
              onClick={handleStartBatch}
              className="flex items-center gap-1.5 rounded-xl bg-emerald-600 px-4 py-2 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
            >
              <Play className="h-3.5 w-3.5" />
              启动批量生成队列
            </button>
          </div>
        </div>

        {/* Batch Job History / Live Queue */}
        <div className="space-y-3">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-400">
            批量队列任务 ({batchJobs.length})
          </h3>
          {batchJobs.map((job) => (
            <div key={job.job_id} className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Clapperboard className="h-4 w-4 text-emerald-400" />
                  <span className="text-sm font-semibold">{job.title}</span>
                  <span className="font-mono text-xs text-zinc-500">{job.session_name}</span>
                </div>
                <span
                  className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                    job.state === "done"
                      ? "bg-emerald-500/20 text-emerald-300"
                      : job.state === "failed"
                        ? "bg-rose-500/20 text-rose-300"
                        : "bg-sky-500/20 text-sky-300"
                  }`}
                >
                  {job.state.toUpperCase()} · {Math.round(job.progress * 100)}%
                </span>
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-zinc-800">
                <div
                  className="h-full bg-emerald-500 transition-all"
                  style={{ width: `${Math.round(job.progress * 100)}%` }}
                />
              </div>
              <p className="mt-2 text-xs text-zinc-400">{job.message}</p>
              <div className="mt-3 grid gap-1.5 sm:grid-cols-2">
                {job.shots.map((sh) => (
                  <div
                    key={sh.index}
                    className="flex items-center justify-between rounded-lg bg-zinc-950 px-2.5 py-1.5 text-xs"
                  >
                    <span className="truncate pr-2 text-zinc-300">
                      S{String(sh.index).padStart(2, "0")}: {sh.subtitle || sh.prompt}
                    </span>
                    <span
                      className={`shrink-0 font-mono text-[11px] ${
                        sh.state === "done"
                          ? "text-emerald-400"
                          : sh.state === "failed"
                            ? "text-rose-400"
                            : "text-zinc-500"
                      }`}
                    >
                      {sh.state}
                    </span>
                  </div>
                ))}
              </div>
              {job.final_artifact_id && (
                <div className="mt-3 flex items-center justify-between rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs">
                  <span className="flex items-center gap-1.5 text-emerald-200">
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    完整带字幕成片已生成
                  </span>
                  <button onClick={onGoOutputs} className="font-semibold text-emerald-300 underline">
                    前往成品库播放 →
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Right: Standalone Multi-Clip Stitcher + Chinese Subtitle Burner */}
      <div className="lg:col-span-5">
        <div className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-5">
          <h2 className="flex items-center gap-2 font-semibold">
            <Subtitles className="h-4 w-4 text-emerald-400" />
            成品库多镜拼接 & 中文字幕压制台
          </h2>
          <p className="mt-1 text-xs text-zinc-400">
            从已有成品库勾选片段（按勾选顺序拼接），填写每段中文台词，本机 ffmpeg 一键硬烧录电影级中文字幕与 `.srt`。
          </p>

          <div className="mt-4">
            <label className="mb-1 block text-xs text-zinc-400">输出成片名称</label>
            <input
              value={composeTitle}
              onChange={(e) => setComposeTitle(e.target.value)}
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
            />
          </div>

          <div className="mt-4 max-h-80 space-y-2 overflow-y-auto pr-1">
            {videoArtifacts.length === 0 ? (
              <div className="rounded-xl border border-dashed border-zinc-800 p-6 text-center text-xs text-zinc-500">
                成品库暂无视频片段，先运行左侧批量队列或收割云端成品即可在此拼接加字幕。
              </div>
            ) : (
              videoArtifacts.map((art) => {
                const orderIdx = selectedIds.indexOf(art.id);
                const checked = orderIdx !== -1;
                return (
                  <div
                    key={art.id}
                    className={`rounded-xl border p-3 transition ${
                      checked ? "border-emerald-500/50 bg-emerald-500/5" : "border-zinc-800 bg-zinc-950/60"
                    }`}
                  >
                    <label className="flex cursor-pointer items-center justify-between gap-2 text-xs">
                      <span className="flex items-center gap-2 truncate font-mono text-zinc-200">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleSelect(art.id)}
                          className="accent-emerald-500"
                        />
                        <Film className="h-3.5 w-3.5 shrink-0 text-zinc-500" />
                        <span className="truncate">{art.filename}</span>
                      </span>
                      {checked && (
                        <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 font-mono text-[11px] text-emerald-300">
                          #{orderIdx + 1}
                        </span>
                      )}
                    </label>
                    {checked && (
                      <input
                        value={composeSubs[art.id] || ""}
                        onChange={(e) => setComposeSubs({ ...composeSubs, [art.id]: e.target.value })}
                        placeholder={`输入第 ${orderIdx + 1} 段中文台词字幕...`}
                        className="mt-2 w-full rounded-lg border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-xs"
                      />
                    )}
                  </div>
                );
              })
            )}
          </div>

          <div className="mt-4 flex items-center justify-between">
            <label className="flex items-center gap-1.5 text-xs text-zinc-300">
              <input
                type="checkbox"
                checked={composeBurn}
                onChange={(e) => setComposeBurn(e.target.checked)}
                className="accent-emerald-500"
              />
              硬烧录中文字幕
            </label>
            <button
              disabled={busy !== null || selectedIds.length === 0}
              onClick={handleCompose}
              className="flex items-center gap-1.5 rounded-xl bg-emerald-600 px-4 py-2 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
            >
              <Wand2 className="h-3.5 w-3.5" />
              合成字幕成片 ({selectedIds.length})
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
