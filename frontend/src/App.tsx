import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Cpu,
  Download,
  ExternalLink,
  Film,
  KeyRound,
  Layers,
  LayoutDashboard,
  Play,
  Power,
  RefreshCw,
  Rocket,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Trash2,
} from "lucide-react";
import { api, type BootstrapData, type RecipeSpec } from "./lib/api";
import BatchStudioTab from "./components/BatchStudioTab";

type Tab = "dashboard" | "deploy" | "batch" | "outputs" | "accounts";

function fmtTimeLeft(deadlineEpoch: number | null): string {
  if (!deadlineEpoch) return "未开启";
  const mins = Math.max(0, Math.round((deadlineEpoch - Date.now() / 1000) / 60));
  return `${mins} 分钟`;
}

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

export default function App() {
  const [data, setData] = useState<BootstrapData | null>(null);
  const [tab, setTab] = useState<Tab>("dashboard");
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  // Deploy form state
  const [selectedRecipeId, setSelectedRecipeId] = useState<string>("");
  const [accel, setAccel] = useState<string>("L4");
  const [minVram, setMinVram] = useState<number>(14000);
  const [reuseSession, setReuseSession] = useState<string>("");
  const [recipeInputs, setRecipeInputs] = useState<Record<string, unknown>>({});

  // OAuth / Secrets form state
  const [oauthFlow, setOauthFlow] = useState<{ flow_id: string; auth_url: string } | null>(null);
  const [oauthCode, setOauthCode] = useState("");
  const [hfToken, setHfToken] = useState("");

  const notify = (kind: "ok" | "err", text: string) => {
    setToast({ kind, text });
    window.setTimeout(() => setToast(null), 4500);
  };

  const refresh = useCallback(async () => {
    try {
      const snap = await api.bootstrap();
      setData(snap);
      if (!selectedRecipeId && snap.recipes.length > 0) {
        const first = snap.recipes[0];
        setSelectedRecipeId(first.id);
        setAccel(first.default_accelerator);
        setMinVram(first.min_vram_mib);
        const defaults: Record<string, unknown> = {};
        first.inputs.forEach((i) => (defaults[i.id] = i.default));
        setRecipeInputs(defaults);
      }
    } catch (e) {
      notify("err", `加载状态失败: ${(e as Error).message}`);
    }
  }, [selectedRecipeId]);

  useEffect(() => {
    refresh();
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/ws/events`);
    ws.onmessage = () => {
      refresh();
    };
    return () => ws.close();
  }, [refresh]);

  const selectRecipe = (r: RecipeSpec) => {
    setSelectedRecipeId(r.id);
    setAccel(r.default_accelerator);
    setMinVram(r.min_vram_mib);
    const defaults: Record<string, unknown> = {};
    r.inputs.forEach((i) => (defaults[i.id] = i.default));
    setRecipeInputs(defaults);
  };

  const runAction = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    try {
      await fn();
    } catch (e) {
      notify("err", (e as Error).message);
    } finally {
      await refresh();
      setBusy(null);
    }
  };

  if (!data) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-zinc-950 text-zinc-400">
        <RefreshCw className="mr-2 h-5 w-5 animate-spin text-emerald-400" />
        正在连接 Colab Studio 控制面...
      </div>
    );
  }

  const isMock = data.settings.backend_mode === "mock";
  const usage = data.active_account?.usage;
  const orphans = data.sessions.filter((s) => !s.managed);
  const managed = data.sessions.filter((s) => s.managed);
  const activeRecipe = data.recipes.find((r) => r.id === selectedRecipeId) || data.recipes[0];
  const latestRun = data.runs[0];

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      {/* Top Bar */}
      <header className="sticky top-0 z-30 border-b border-zinc-800/80 bg-zinc-950/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-5 py-3.5">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/30">
              <Film className="h-5 w-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="font-semibold tracking-tight">Colab Studio</span>
                <button
                  onClick={() =>
                    runAction("mode", async () => {
                      const next = isMock ? "real" : "mock";
                      await api.patchSettings({ backend_mode: next });
                      notify("ok", next === "mock" ? "已切回零计费沙盒模式" : "已切换至真实 Google Colab 模式");
                    })
                  }
                  className={`rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 transition ${
                    isMock
                      ? "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30 hover:bg-emerald-500/25"
                      : "bg-amber-500/15 text-amber-300 ring-amber-500/40 hover:bg-amber-500/25"
                  }`}
                >
                  {isMock ? "SANDBOX · 0 计费" : "REAL COLAB · 真实计费"}
                </button>
              </div>
              <p className="text-xs text-zinc-500">Google Colab 视频生成算力工作台</p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <div className="hidden items-center gap-3 rounded-xl border border-zinc-800 bg-zinc-900/70 px-3.5 py-1.5 text-xs sm:flex">
              <span className="text-zinc-400">{data.active_account?.email || "未登录账号"}</span>
              <span className="h-3.5 w-px bg-zinc-800" />
              <span className="font-mono font-semibold text-emerald-400">
                {usage?.balance_ccu != null ? `${usage.balance_ccu.toFixed(2)} CCU` : "-- CCU"}
              </span>
              <span className="text-zinc-500">
                {usage?.burn_rate_hourly != null ? `${usage.burn_rate_hourly.toFixed(2)}/hr` : ""}
              </span>
            </div>

            <button
              onClick={() => refresh()}
              className="rounded-lg border border-zinc-800 bg-zinc-900 p-2 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
              title="刷新状态"
            >
              <RefreshCw className={`h-4 w-4 ${busy ? "animate-spin" : ""}`} />
            </button>

            <button
              disabled={busy !== null}
              onClick={() =>
                runAction("stop-all", async () => {
                  const res = await api.stopAll();
                  notify("ok", `已释放 ${res.stopped_count} 台实例，零残留核验: ${res.zero_burn_verified ? "通过" : "请复查"}`);
                })
              }
              className="flex items-center gap-1.5 rounded-lg bg-rose-600 px-3.5 py-2 text-xs font-semibold text-white shadow-sm hover:bg-rose-500 disabled:opacity-50"
            >
              <Power className="h-3.5 w-3.5" />
              一键全关
            </button>
          </div>
        </div>

        {/* Nav Tabs */}
        <div className="mx-auto flex max-w-7xl gap-1 px-5">
          {[
            { id: "dashboard", label: "算力总览", icon: LayoutDashboard, count: data.sessions.length },
            { id: "deploy", label: "极速部署", icon: Rocket, count: data.recipes.length },
            { id: "batch", label: "批量队列与字幕", icon: Layers, count: data.batch_jobs.length },
            { id: "outputs", label: "成品库", icon: Film, count: data.artifacts.length },
            { id: "accounts", label: "账号与护栏", icon: KeyRound, count: data.accounts.length },
          ].map((item) => {
            const Icon = item.icon;
            const active = tab === item.id;
            return (
              <button
                key={item.id}
                onClick={() => setTab(item.id as Tab)}
                className={`flex items-center gap-2 border-b-2 px-4 py-2.5 text-sm font-medium transition ${
                  active
                    ? "border-emerald-400 text-emerald-300"
                    : "border-transparent text-zinc-400 hover:text-zinc-200"
                }`}
              >
                <Icon className="h-4 w-4" />
                {item.label}
                <span className="rounded-full bg-zinc-800 px-1.5 py-0.5 text-[11px] text-zinc-300">{item.count}</span>
              </button>
            );
          })}
        </div>
      </header>

      {/* Toast */}
      {toast && (
        <div className="fixed right-5 bottom-5 z-50">
          <div
            className={`flex items-center gap-2 rounded-xl border px-4 py-3 text-sm shadow-xl ${
              toast.kind === "ok"
                ? "border-emerald-500/40 bg-emerald-950/90 text-emerald-200"
                : "border-rose-500/40 bg-rose-950/90 text-rose-200"
            }`}
          >
            {toast.kind === "ok" ? <CheckCircle2 className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />}
            {toast.text}
          </div>
        </div>
      )}

      <main className="mx-auto max-w-7xl px-5 py-6">
        {/* TAB 1: DASHBOARD */}
        {tab === "dashboard" && (
          <div className="space-y-6">
            {/* Orphan Adoption Banner */}
            {orphans.length > 0 && (
              <div className="rounded-2xl border border-amber-500/35 bg-amber-500/10 p-4">
                <div className="mb-3 flex items-center gap-2 text-amber-300">
                  <ShieldAlert className="h-5 w-5" />
                  <h2 className="font-semibold">检测到 {orphans.length} 台网页端未受管 GPU 实例（无自动关机看门狗）</h2>
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  {orphans.map((s) => (
                    <div
                      key={s.endpoint}
                      className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-500/25 bg-zinc-950/70 p-3.5"
                    >
                      <div>
                        <div className="flex items-center gap-2 font-mono text-sm font-semibold text-zinc-100">
                          {s.name}
                          <span className="rounded bg-amber-500/20 px-1.5 py-0.5 text-xs text-amber-300">
                            {s.accelerator}
                          </span>
                        </div>
                        <p className="mt-0.5 text-xs text-zinc-400">
                          约 {s.estimated_burn_hourly.toFixed(2)} CCU/hr · 接管后自动注入看门狗并可直接部署 ComfyUI
                        </p>
                      </div>
                      <div className="flex gap-2">
                        <button
                          disabled={busy !== null}
                          onClick={() =>
                            runAction("adopt", async () => {
                              const adopted = await api.adoptSession(s.endpoint);
                              notify("ok", `已接管 ${adopted.name} 并自动挂载防忘关机看门狗`);
                            })
                          }
                          className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
                        >
                          {busy === "adopt" ? (
                            <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <ShieldCheck className="h-3.5 w-3.5" />
                          )}
                          {busy === "adopt" ? "接管中..." : "一键接管 + 挂看门狗"}
                        </button>
                        <button
                          disabled={busy !== null}
                          onClick={() =>
                            runAction("stop-orphan", async () => {
                              await api.stopSession(s.name);
                              notify("ok", `已销毁未受管实例 ${s.name}`);
                            })
                          }
                          className="rounded-lg border border-rose-500/40 px-2.5 py-1.5 text-xs text-rose-300 hover:bg-rose-500/15 disabled:opacity-50"
                        >
                          {busy === "stop-orphan" ? "销毁中..." : "销毁"}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Managed Sessions */}
            <div>
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-sm font-semibold uppercase tracking-wider text-zinc-400">
                  受管运行中 GPU 实例 ({managed.length})
                </h2>
                <button
                  onClick={() => setTab("deploy")}
                  className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500"
                >
                  <Sparkles className="h-3.5 w-3.5" />
                  新建视频工作台
                </button>
              </div>

              {managed.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-zinc-800 bg-zinc-900/30 p-10 text-center text-sm text-zinc-500">
                  当前没有受管运行中的 GPU 实例（0 计费）。前往「极速部署」开卡，或点击上方「一键接管」网页端实例。
                </div>
              ) : (
                <div className="grid gap-4 md:grid-cols-2">
                  {managed.map((s) => (
                    <div key={s.endpoint} className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-5">
                      <div className="flex items-start justify-between">
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="h-2.5 w-2.5 rounded-full bg-emerald-400" />
                            <h3 className="font-mono text-sm font-semibold">{s.name}</h3>
                            <span className="rounded-md bg-emerald-500/15 px-2 py-0.5 text-xs font-medium text-emerald-300">
                              {s.gpu_name || s.accelerator}
                            </span>
                            {s.adopted && (
                              <span className="rounded-md bg-sky-500/15 px-2 py-0.5 text-xs text-sky-300">网页接管</span>
                            )}
                          </div>
                          <p className="mt-1 text-xs text-zinc-400">
                            估算消耗 {s.estimated_burn_hourly.toFixed(2)} CCU/hr · 显存{" "}
                            {s.vram_total_mib ? `${Math.round(s.vram_total_mib / 1024)} GB` : "探测中"}
                          </p>
                        </div>
                        <button
                          disabled={busy !== null}
                          onClick={() =>
                            runAction("stop", async () => {
                              await api.stopSession(s.name);
                              notify("ok", `已释放 ${s.name} 并生成关闭回执`);
                            })
                          }
                          className="rounded-lg border border-rose-500/30 px-2.5 py-1 text-xs text-rose-300 hover:bg-rose-500/15"
                        >
                          停止释放
                        </button>
                      </div>

                      {/* Watchdog Bar */}
                      <div className="mt-4 flex items-center justify-between rounded-xl bg-zinc-950/70 px-3 py-2 text-xs">
                        <span className="flex items-center gap-1.5 text-emerald-300">
                          <Clock className="h-3.5 w-3.5" />
                          看门狗护栏运行中 · 剩余 {fmtTimeLeft(s.watchdog.deadline_epoch)}
                        </span>
                        <button
                          onClick={() =>
                            runAction("extend", async () => {
                              await api.extendSession(s.name, 30);
                              notify("ok", `${s.name} 看门狗已延长 30 分钟`);
                            })
                          }
                          className="text-zinc-300 underline hover:text-white"
                        >
                          +30 分钟
                        </button>
                      </div>

                      {/* Tunnels & Harvest Actions */}
                      <div className="mt-4 flex flex-wrap gap-2">
                        {s.tunnels.map((t) => (
                          <a
                            key={t.id}
                            href={t.local_url}
                            target="_blank"
                            rel="noreferrer"
                            className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500"
                          >
                            <ExternalLink className="h-3.5 w-3.5" />
                            打开 {t.label} (:{t.local_port})
                          </a>
                        ))}
                        <button
                          disabled={busy !== null}
                          onClick={() =>
                            runAction("harvest", async () => {
                              const r = await api.harvestSession(s.name, false);
                              notify("ok", `已拉取 ${r.harvested.length} 个成品到本地成品库`);
                              setTab("outputs");
                            })
                          }
                          className="flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-xs font-medium hover:bg-zinc-700"
                        >
                          <Download className="h-3.5 w-3.5" />
                          收割成品
                        </button>
                        <button
                          disabled={busy !== null}
                          onClick={() =>
                            runAction("harvest-stop", async () => {
                              const r = await api.harvestSession(s.name, true);
                              notify("ok", `已收割 ${r.harvested.length} 个成品并自动释放 GPU`);
                              setTab("outputs");
                            })
                          }
                          className="flex items-center gap-1.5 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs font-medium text-amber-200 hover:bg-amber-500/20"
                        >
                          收割并关机
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* TAB 2: DEPLOY STUDIO */}
        {tab === "deploy" && (
          <div className="grid gap-6 lg:grid-cols-12">
            {/* Recipe Selector */}
            <div className="space-y-3 lg:col-span-4">
              <h2 className="text-sm font-semibold uppercase tracking-wider text-zinc-400">视频生成配方</h2>
              {data.recipes.map((r) => (
                <button
                  key={r.id}
                  onClick={() => selectRecipe(r)}
                  className={`w-full rounded-2xl border p-4 text-left transition ${
                    selectedRecipeId === r.id
                      ? "border-emerald-500/60 bg-emerald-500/10"
                      : "border-zinc-800 bg-zinc-900/50 hover:border-zinc-700"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-zinc-100">{r.name}</span>
                    <span className="rounded bg-zinc-800 px-2 py-0.5 text-xs text-emerald-300">
                      {r.default_accelerator}
                    </span>
                  </div>
                  <p className="mt-1.5 text-xs leading-relaxed text-zinc-400">{r.description}</p>
                  <div className="mt-2.5 flex flex-wrap gap-1.5">
                    {r.tags.map((t) => (
                      <span key={t} className="rounded-md bg-zinc-800/80 px-2 py-0.5 text-[11px] text-zinc-300">
                        {t}
                      </span>
                    ))}
                  </div>
                </button>
              ))}
            </div>

            {/* Config Form + Live Console */}
            <div className="space-y-5 lg:col-span-8">
              {activeRecipe && (
                <div className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-5">
                  <h3 className="text-base font-semibold">{activeRecipe.name}</h3>
                  <div className="mt-4 grid gap-4 sm:grid-cols-3">
                    <div>
                      <label className="mb-1 block text-xs text-zinc-400">目标 GPU 卡型</label>
                      <select
                        value={accel}
                        onChange={(e) => setAccel(e.target.value)}
                        className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
                      >
                        {["T4", "L4", "A100", "G4", "H100"].map((g) => (
                          <option key={g} value={g}>
                            {g}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="mb-1 block text-xs text-zinc-400">最低显存保底 (MiB)</label>
                      <input
                        type="number"
                        value={minVram}
                        onChange={(e) => setMinVram(Number(e.target.value))}
                        className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
                      />
                    </div>
                    <div>
                      <label className="mb-1 block text-xs text-zinc-400">复用已有实例 (绕过 503)</label>
                      <select
                        value={reuseSession}
                        onChange={(e) => setReuseSession(e.target.value)}
                        className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
                      >
                        <option value="">自动申请新 GPU 实例</option>
                        {data.sessions.map((s) => (
                          <option key={s.endpoint} value={s.name}>
                            {s.name} ({s.accelerator})
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>

                  {/* Dynamic Recipe Inputs */}
                  <div className="mt-4 grid gap-4 sm:grid-cols-2">
                    {activeRecipe.inputs.map((inp) => (
                      <div key={inp.id}>
                        <label className="mb-1 block text-xs text-zinc-300">{inp.label}</label>
                        {inp.type === "select" ? (
                          <select
                            value={String(recipeInputs[inp.id] ?? inp.default)}
                            onChange={(e) => setRecipeInputs({ ...recipeInputs, [inp.id]: e.target.value })}
                            className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
                          >
                            {inp.options.map((opt) => (
                              <option key={opt} value={opt}>
                                {opt}
                              </option>
                            ))}
                          </select>
                        ) : inp.type === "bool" ? (
                          <label className="flex items-center gap-2 pt-1 text-sm text-zinc-300">
                            <input
                              type="checkbox"
                              checked={Boolean(recipeInputs[inp.id] ?? inp.default)}
                              onChange={(e) => setRecipeInputs({ ...recipeInputs, [inp.id]: e.target.checked })}
                              className="h-4 w-4 accent-emerald-500"
                            />
                            启用
                          </label>
                        ) : (
                          <input
                            type={inp.type === "number" ? "number" : "text"}
                            value={String(recipeInputs[inp.id] ?? inp.default)}
                            onChange={(e) =>
                              setRecipeInputs({
                                ...recipeInputs,
                                [inp.id]: inp.type === "number" ? Number(e.target.value) : e.target.value,
                              })
                            }
                            className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
                          />
                        )}
                        {inp.help && <p className="mt-1 text-[11px] text-zinc-500">{inp.help}</p>}
                      </div>
                    ))}
                  </div>

                  <div className="mt-5 flex justify-end">
                    <button
                      disabled={busy !== null}
                      onClick={() =>
                        runAction("deploy", async () => {
                          await api.deploy({
                            recipe_id: activeRecipe.id,
                            accelerator: accel,
                            min_vram_mib: minVram,
                            session_name: reuseSession || undefined,
                            inputs: recipeInputs,
                          });
                          notify("ok", `已启动 ${activeRecipe.name} 部署任务`);
                        })
                      }
                      className="flex items-center gap-2 rounded-xl bg-emerald-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
                    >
                      <Play className="h-4 w-4" />
                      一键部署并映射到本地
                    </button>
                  </div>
                </div>
              )}

              {/* Latest Run Log Panel */}
              {latestRun && (
                <div className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-5">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Activity className="h-4 w-4 text-emerald-400" />
                      <span className="text-sm font-semibold">{latestRun.recipe_name}</span>
                      <span className="font-mono text-xs text-zinc-500">{latestRun.session_name}</span>
                    </div>
                    <span
                      className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                        latestRun.state === "ready"
                          ? "bg-emerald-500/20 text-emerald-300"
                          : latestRun.state === "failed"
                            ? "bg-rose-500/20 text-rose-300"
                            : "bg-sky-500/20 text-sky-300"
                      }`}
                    >
                      {latestRun.state.toUpperCase()} · {Math.round(latestRun.progress * 100)}%
                    </span>
                  </div>
                  <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-zinc-800">
                    <div
                      className="h-full bg-emerald-500 transition-all"
                      style={{ width: `${Math.round(latestRun.progress * 100)}%` }}
                    />
                  </div>
                  <p className="mt-2 text-xs text-zinc-300">{latestRun.message}</p>
                  <pre className="mt-3 max-h-52 overflow-y-auto rounded-xl bg-zinc-950 p-3 font-mono text-xs text-zinc-400">
                    {latestRun.logs.join("\n") || "等待日志输出..."}
                  </pre>
                </div>
              )}
            </div>
          </div>
        )}

        {/* TAB 2.5: BATCH QUEUE & SUBTITLE STUDIO */}
        {tab === "batch" && (
          <BatchStudioTab
            sessions={data.sessions}
            batchJobs={data.batch_jobs}
            artifacts={data.artifacts}
            busy={busy}
            runAction={runAction}
            notify={notify}
            onGoOutputs={() => setTab("outputs")}
          />
        )}

        {/* TAB 3: OUTPUTS GALLERY */}
        {tab === "outputs" && (
          <div className="space-y-4">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-zinc-400">
              本地成品库 ({data.artifacts.length})
            </h2>
            {data.artifacts.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-zinc-800 bg-zinc-900/30 p-10 text-center text-sm text-zinc-500">
                暂无已收割的视频成品。在「算力总览」点击实例的「收割成品」按钮即可自动拉取并校验落盘。
              </div>
            ) : (
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {data.artifacts.map((art) => (
                  <div key={art.id} className="overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-900/60">
                    {art.media_type === "video" ? (
                      <video
                        controls
                        src={`/api/artifacts/${art.id}/file`}
                        className="aspect-video w-full bg-black object-cover"
                      />
                    ) : (
                      <img
                        src={`/api/artifacts/${art.id}/file`}
                        alt={art.filename}
                        className="aspect-video w-full bg-black object-cover"
                      />
                    )}
                    <div className="p-4">
                      <div className="truncate font-mono text-sm font-semibold">{art.filename}</div>
                      <div className="mt-1 flex items-center justify-between text-xs text-zinc-400">
                        <span>{art.session_name}</span>
                        <span>{fmtSize(art.size_bytes)}</span>
                      </div>
                      <div className="mt-2 flex items-center justify-between text-[11px] text-zinc-500">
                        <span className="font-mono">SHA256: {art.sha256.slice(0, 12)}…</span>
                        <a
                          href={`/api/artifacts/${art.id}/file`}
                          download={art.filename}
                          className="text-emerald-400 hover:underline"
                        >
                          下载
                        </a>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* TAB 4: ACCOUNTS, GUARDS & RECEIPTS */}
        {tab === "accounts" && (
          <div className="grid gap-6 lg:grid-cols-2">
            {/* Accounts & OAuth */}
            <div className="space-y-4 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-5">
              <div className="flex items-center justify-between">
                <h2 className="font-semibold">多 Google 账号凭据仓</h2>
                <button
                  onClick={() =>
                    runAction("oauth-start", async () => {
                      const flow = await api.oauthStart();
                      setOauthFlow(flow);
                    })
                  }
                  className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500"
                >
                  + 绑定 Google 账号
                </button>
              </div>

              {oauthFlow && (
                <div className="space-y-2 rounded-xl border border-emerald-500/30 bg-zinc-950 p-3 text-xs">
                  <p className="text-zinc-300">
                    1. 点击授权链接完成登录：
                    <a
                      href={oauthFlow.auth_url}
                      target="_blank"
                      rel="noreferrer"
                      className="ml-1 text-emerald-400 underline"
                    >
                      打开 Google OAuth 页面
                    </a>
                  </p>
                  <div className="flex gap-2">
                    <input
                      value={oauthCode}
                      onChange={(e) => setOauthCode(e.target.value)}
                      placeholder="粘贴授权码 (Auth Code)"
                      className="flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-2.5 py-1.5"
                    />
                    <button
                      onClick={() =>
                        runAction("oauth-done", async () => {
                          await api.oauthComplete(oauthFlow.flow_id, oauthCode);
                          setOauthFlow(null);
                          setOauthCode("");
                          notify("ok", "Google 账号绑定成功");
                        })
                      }
                      className="rounded-lg bg-emerald-600 px-3 py-1.5 font-semibold text-white"
                    >
                      完成
                    </button>
                  </div>
                </div>
              )}

              <div className="space-y-2">
                {data.accounts.length === 0 && (
                  <div className="rounded-xl border border-dashed border-zinc-800 p-4 text-center text-xs text-zinc-500">
                    尚未绑定任何真实 Google 账号，点击右上角「+ 绑定 Google 账号」即可登录。
                  </div>
                )}
                {data.accounts.map((a) => (
                  <div
                    key={a.id}
                    className={`flex items-center justify-between rounded-xl border p-3 ${
                      a.is_active ? "border-emerald-500/50 bg-emerald-500/5" : "border-zinc-800 bg-zinc-950/60"
                    }`}
                  >
                    <div>
                      <div className="text-sm font-medium">{a.email}</div>
                      <div className="text-xs text-zinc-400">
                        余额 {a.usage.balance_ccu?.toFixed(2) ?? "--"} CCU · 卡型{" "}
                        {a.usage.eligible_gpus.join("/") || "--"}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      {a.is_active ? (
                        <span className="rounded-full bg-emerald-500/20 px-2.5 py-0.5 text-xs text-emerald-300">
                          当前使用
                        </span>
                      ) : (
                        <button
                          onClick={() => runAction("activate", () => api.activateAccount(a.id).then(() => undefined))}
                          className="rounded-lg border border-zinc-700 px-2.5 py-1 text-xs hover:bg-zinc-800"
                        >
                          切换
                        </button>
                      )}
                      {a.id !== "acc-mock-sandbox" && (
                        <button
                          onClick={() =>
                            runAction("del-acc", async () => {
                              await api.deleteAccount(a.id);
                              notify("ok", `已移除账号 ${a.email}`);
                            })
                          }
                          className="rounded-lg p-1.5 text-zinc-500 hover:bg-rose-500/15 hover:text-rose-400"
                          title="移除账号"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>

              {/* HF Token */}
              <div className="border-t border-zinc-800 pt-4">
                <h3 className="mb-2 text-sm font-semibold">模型仓库密钥（OS Keyring 加密存储）</h3>
                <div className="flex gap-2">
                  <input
                    type="password"
                    value={hfToken}
                    onChange={(e) => setHfToken(e.target.value)}
                    placeholder="输入 Hugging Face Token (hf_...)"
                    className="flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-xs"
                  />
                  <button
                    onClick={() =>
                      runAction("save-hf", async () => {
                        if (!hfToken.trim()) return;
                        await api.saveSecret("hf_token", hfToken.trim());
                        setHfToken("");
                        notify("ok", "HF Token 已加密存入系统钥匙串");
                      })
                    }
                    className="rounded-lg bg-zinc-800 px-3 py-1.5 text-xs font-medium hover:bg-zinc-700"
                  >
                    保存
                  </button>
                </div>
                <div className="mt-2 flex flex-wrap gap-2">
                  {data.secret_names.map((name) => (
                    <span
                      key={name}
                      className="flex items-center gap-1.5 rounded-md bg-zinc-800 px-2 py-1 font-mono text-xs text-emerald-300"
                    >
                      {name}
                      <button
                        onClick={() => runAction("del-secret", () => api.deleteSecret(name).then(() => undefined))}
                        className="text-zinc-400 hover:text-rose-400"
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    </span>
                  ))}
                </div>
              </div>
            </div>

            {/* Cost Guards & Unassign Receipts */}
            <div className="space-y-4">
              <div className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-5">
                <h2 className="mb-3 flex items-center gap-2 font-semibold">
                  <Cpu className="h-4 w-4 text-emerald-400" />
                  防烧钱看门狗护栏策略
                </h2>
                <div className="grid grid-cols-2 gap-3 text-xs">
                  <div className="rounded-xl bg-zinc-950 p-3">
                    <div className="text-zinc-400">单次最长运行</div>
                    <div className="mt-1 font-mono text-base font-semibold">
                      {data.settings.guards.auto_stop_minutes} 分钟
                    </div>
                  </div>
                  <div className="rounded-xl bg-zinc-950 p-3">
                    <div className="text-zinc-400">GPU 闲置自动关机</div>
                    <div className="mt-1 font-mono text-base font-semibold">
                      {data.settings.guards.idle_stop_minutes} 分钟
                    </div>
                  </div>
                  <div className="rounded-xl bg-zinc-950 p-3">
                    <div className="text-zinc-400">CCU 余额警戒线</div>
                    <div className="mt-1 font-mono text-base font-semibold">
                      {data.settings.guards.balance_floor_ccu} CCU
                    </div>
                  </div>
                  <div className="rounded-xl bg-zinc-950 p-3">
                    <div className="text-zinc-400">显存不足重抽次数</div>
                    <div className="mt-1 font-mono text-base font-semibold">
                      {data.settings.guards.max_vram_retries} 次
                    </div>
                  </div>
                </div>
              </div>

              <div className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-5">
                <h2 className="mb-3 font-semibold">关闭回执审计 ({data.receipts.length})</h2>
                {data.receipts.length === 0 ? (
                  <p className="text-xs text-zinc-500">暂无停机回执记录。</p>
                ) : (
                  <div className="max-h-64 space-y-2 overflow-y-auto">
                    {data.receipts.map((rc) => (
                      <div
                        key={rc.receipt_id}
                        className="flex items-center justify-between rounded-xl bg-zinc-950 px-3 py-2 text-xs"
                      >
                        <div>
                          <div className="font-mono font-semibold text-zinc-200">
                            {rc.session_name} ({rc.accelerator})
                          </div>
                          <div className="text-zinc-500">
                            {rc.reason} · 估算 {rc.estimated_ccu_spent} CCU
                          </div>
                        </div>
                        <span className="rounded bg-emerald-500/15 px-2 py-0.5 text-emerald-300">
                          {rc.verified_unassigned ? "已核验清零" : "待复核"}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
