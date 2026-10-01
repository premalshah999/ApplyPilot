import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronRight,
  Circle,
  Clock3,
  FileText,
  Gauge,
  Layers3,
  LogOut,
  Pause,
  Play,
  Plus,
  Radio,
  RefreshCw,
  Search,
  Send,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  SquareArrowOutUpRight,
  Upload,
  UserRound,
  X,
  Zap,
} from "lucide-react";
import type {
  Snapshot,
  Job,
  Run,
  Profile,
  Detail,
  Source,
  Review,
} from "./types";
import "@fontsource-variable/dm-sans";
import "@fontsource-variable/manrope";
import "./style.css";

async function api<T = any>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const form = body instanceof FormData;
  const response = await fetch("/api" + path, {
    method,
    credentials: "same-origin",
    headers:
      method === "GET" || method === "DELETE"
        ? {}
        : form
          ? {}
          : { "Content-Type": "application/json" },
    body:
      body === undefined
        ? method === "POST"
          ? "{}"
          : undefined
        : form
          ? body
          : JSON.stringify(body),
  });
  const data = await response
    .json()
    .catch(() => ({ detail: "Server returned an invalid response" }));
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event("signed-out"));
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail),
    );
  }
  return data;
}
const label = (s: string) => s.replaceAll("_", " ");
const money = (n: number) => "$" + n.toFixed(3);
const when = (s: string | null) =>
  s
    ? new Date(s).toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "Not run yet";
const short = (s: string) =>
  s
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((x) => x[0])
    .join("")
    .toUpperCase() || "JP";
const good = ["confirmed", "ready", "dry_run_passed"];
const active = ["queued", "running", "submitting"];
type Action = (fn: () => Promise<unknown>, success?: string) => Promise<void>;
function Badge({ value }: { value: string }) {
  return (
    <span
      className={
        "badge " +
        (good.includes(value)
          ? "good"
          : active.includes(value)
            ? "live"
            : value.includes("review") ||
                value.includes("unknown") ||
                value === "timed_out"
              ? "warn"
              : "")
      }
    >
      <i />
      {label(value)}
    </span>
  );
}
function Empty({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="empty">
      <Layers3 size={28} />
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
function Field({
  title,
  children,
  hint,
}: {
  title: string;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <label className="field">
      <span>{title}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}

function App() {
  const [data, setData] = useState<Snapshot | null>(null),
    [authenticated, setAuthenticated] = useState(true);
  const [page, setPage] = useState("Overview"),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    [error, setError] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const refresh = async () => setData(await api<Snapshot>("/snapshot"));
  useEffect(() => {
    const signout = () => {
      setAuthenticated(false);
      setData(null);
    };
    window.addEventListener("signed-out", signout);
    refresh().catch((e) => setError(e.message));
    return () => window.removeEventListener("signed-out", signout);
  }, []);
  useEffect(() => {
    if (!authenticated) return;
    const timer = setInterval(() => refresh().catch(() => {}), 3000);
    return () => clearInterval(timer);
  }, [authenticated]);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 6500);
    return () => clearTimeout(timer);
  }, [notice]);
  const act: Action = async (fn, message) => {
    setBusy(true);
    setError("");
    try {
      await fn();
      await refresh();
      if (message) setNotice(message);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const inspect = async (run: Run) =>
    act(async () => setDetail(await api<Detail>("/runs/" + run.id)));
  if (!authenticated)
    return (
      <Login
        onLogin={async () => {
          setAuthenticated(true);
          setError("");
          await refresh();
        }}
      />
    );
  if (!data)
    return (
      <div className="boot">
        <div className="brand-icon">
          <Zap />
        </div>
        <h2>Opening your control room</h2>
        {error ? (
          <p>
            {error} <button onClick={() => location.reload()}>Retry</button>
          </p>
        ) : (
          <p>Connecting to ApplyPilot…</p>
        )}
      </div>
    );
  const nav = [
    ["Overview", Gauge],
    ["Applications", Layers3],
    ["Review inbox", ShieldCheck],
    ["Resumes", FileText],
    ["Knowledge base", UserRound],
    ["Sources", Radio],
    ["Settings", Settings2],
  ] as const;
  return (
    <div className="shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setPage("Overview");
          }}
        >
          <span className="brand-icon">
            <Zap size={21} />
          </span>
          <span>
            ApplyPilot<small>PERSONAL WORKSPACE</small>
          </span>
        </a>
        <div className="nav-label">WORKSPACE</div>
        <nav>
          {nav.map(([name, Icon]) => (
            <button
              key={name}
              aria-label={name}
              title={name}
              className={page === name ? "selected" : ""}
              onClick={() => setPage(name)}
            >
              <Icon size={18} />
              <span>{name}</span>
              {name === "Review inbox" && data.reviews.length > 0 && (
                <b>{data.reviews.length}</b>
              )}
            </button>
          ))}
        </nav>
        <div className="side-bottom">
          <div className="worker-card">
            <span className={"dot " + (data.control.paused ? "amber" : "")} />
            <strong>
              {data.control.paused ? "Workers paused" : "System ready"}
            </strong>
            <small>
              {data.config.workers} browser workers · {data.config.timeout}s
              limit
            </small>
            <div className="micro-track">
              <i
                style={{
                  width:
                    Math.min(
                      100,
                      (data.stats.submissions_reserved /
                        data.config.daily_limit) *
                        100,
                    ) + "%",
                }}
              />
            </div>
            <small>
              {data.stats.submissions_reserved} / {data.config.daily_limit}{" "}
              daily submissions
            </small>
          </div>
          <button className="account" onClick={() => setPage("Knowledge base")}>
            <span className="avatar">
              {short(data.profile.name || "Your workspace")}
            </span>
            <span>
              <strong>{data.profile.name || "Your workspace"}</strong>
              <small>Self-hosted · Private</small>
            </span>
            <Settings2 size={16} />
          </button>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <span>
            Workspace <ChevronRight size={13} /> <b>{page}</b>
          </span>
          <div className="top-actions">
            <span className="private">
              <ShieldCheck size={14} /> Local control
            </span>
            <button
              className="icon-button"
              title="Refresh"
              aria-label="Refresh"
              onClick={() => act(refresh)}
            >
              <RefreshCw size={17} className={busy ? "spin" : ""} />
            </button>
            <button
              className="button compact"
              onClick={() =>
                act(() =>
                  api("/control", "PUT", {
                    ...data.control,
                    paused: !data.control.paused,
                  }),
                )
              }
            >
              {data.control.paused ? <Play size={14} /> : <Pause size={14} />}{" "}
              {data.control.paused ? "Resume" : "Pause"} workers
            </button>
          </div>
        </header>
        <div className="content">
          <div className="page-heading">
            <div className="eyebrow">
              {page === "Overview"
                ? "YOUR NEXT CHAPTER STARTS HERE"
                : "APPLYPILOT STUDIO"}
            </div>
            <div className="heading-line">
              <div>
                <h1>
                  {page === "Overview"
                    ? "A clearer path to your next role."
                    : page}
                </h1>
                <p>
                  {
                    (
                      {
                        Overview:
                          "Discover the right roles. Put every application in motion.",
                        Applications:
                          "A complete record, from first match to confirmed submission.",
                        "Review inbox":
                          "Resolve the questions that need your judgement.",
                        Resumes:
                          "A small library of strong resumes. The right one for each role.",
                        "Knowledge base":
                          "Your verified facts are the source of every answer.",
                        Sources:
                          "Keep fresh opportunities flowing into your workspace.",
                        Settings:
                          "Your integrations, budgets, and operating controls.",
                      } as Record<string, string>
                    )[page]
                  }
                </p>
              </div>
              {page === "Overview" && (
                <button
                  className="button primary"
                  onClick={() => setPage("Applications")}
                >
                  <Plus size={16} /> Add opportunities
                </button>
              )}
            </div>
          </div>
          {error && (
            <div role="alert" className="alert">
              <span>{error}</span>
              <button aria-label="Dismiss error" onClick={() => setError("")}>
                <X size={17} />
              </button>
            </div>
          )}
          {notice && (
            <div role="status" className="notice">
              <CheckCircle2 size={17} />
              {notice}
            </div>
          )}
          {busy && <div className="busy-line" />}
          {page === "Overview" && (
            <Overview data={data} go={setPage} inspect={inspect} act={act} />
          )}
          {page === "Applications" && (
            <Applications data={data} act={act} inspect={inspect} busy={busy} />
          )}
          {page === "Review inbox" && (
            <Reviews data={data} act={act} go={setPage} />
          )}
          {page === "Resumes" && <Resumes data={data} act={act} />}
          {page === "Knowledge base" && (
            <Knowledge initial={data.profile} act={act} />
          )}
          {page === "Sources" && <Sources data={data} act={act} />}
          {page === "Settings" && <Settings data={data} act={act} />}
          <footer>
            <span>
              <span className="dot" /> Your applications. Your control.
            </span>
            <span>ApplyPilot Studio 1.0</span>
          </footer>
        </div>
      </main>
      {detail && (
        <RunDrawer
          detail={detail}
          data={data}
          close={() => setDetail(null)}
          act={act}
          reload={() => api<Detail>("/runs/" + detail.run.id).then(setDetail)}
        />
      )}
    </div>
  );
}

function Login({ onLogin }: { onLogin: () => Promise<void> }) {
  const [token, setToken] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <div className="login">
      <div className="login-story">
        <div className="brand">
          <span className="brand-icon">
            <Zap />
          </span>
          ApplyPilot
        </div>
        <div>
          <span className="eyebrow">LESS REPETITION. MORE POSSIBILITY.</span>
          <h1>
            Make your next
            <br />
            move count.
          </h1>
          <p>
            A private workspace for thoughtful applications,
            <br />
            verified answers, and a little more momentum.
          </p>
        </div>
        <small>BUILT FOR YOUR NEXT CHAPTER</small>
      </div>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await api("/login", "POST", { token });
            await onLogin();
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <span className="eyebrow">WELCOME TO YOUR WORKSPACE</span>
        <h2>Let’s get you in.</h2>
        <p>Use your installation’s access token to open Studio.</p>
        <Field title="Access token">
          <input
            autoFocus
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            required
            autoComplete="current-password"
            placeholder="Paste your access token"
          />
        </Field>
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
        <button className="button primary" disabled={busy}>
          {busy ? "Opening…" : "Open workspace"}
          <ArrowRight size={17} />
        </button>
        <div className="token-help">
          <ShieldCheck size={18} />
          <span>
            Run <code>jobpilot token</code> in your installation.
            <br />
            Docker: <code>docker compose exec app jobpilot token</code>
          </span>
        </div>
      </form>
    </div>
  );
}

function Overview({
  data,
  go,
  inspect,
  act,
}: {
  data: Snapshot;
  go: (x: string) => void;
  inspect: (r: Run) => void;
  act: Action;
}) {
  const stats = [
    [
      "Confirmed today",
      data.stats.confirmed_today,
      `of ${data.config.daily_limit} daily target`,
      Send,
    ],
    [
      "In motion",
      data.runs.filter((r) => active.includes(r.state)).length,
      `${data.config.workers} parallel workers`,
      Activity,
    ],
    [
      "Needs your input",
      data.reviews.length,
      "Answers awaiting review",
      ShieldCheck,
    ],
    [
      "Model spend",
      money(data.stats.spend_today),
      `${money(data.config.budget)} daily budget`,
      Zap,
    ],
  ] as const;
  const steps = [
    {
      ok: !!data.profile.name && !!data.profile.email,
      title: "Add your verified profile",
      text: "Identity, experience, and application facts",
      page: "Knowledge base",
    },
    {
      ok: data.resumes.length > 0,
      title: "Build your resume library",
      text: "Upload up to 10 ready-to-send PDFs",
      page: "Resumes",
    },
    {
      ok: data.config.mimo,
      title: "Connect your application brain",
      text: "Add the MiMo API key in your .env file",
      page: "Settings",
    },
    {
      ok: data.sources.length > 0 || data.jobs.some((j) => !j.demo),
      title: "Find your first opportunities",
      text: "Connect a board or paste a job link",
      page: "Sources",
    },
  ];
  const complete = steps.filter((s) => s.ok).length;
  return (
    <>
      <div className="stats-grid">
        {stats.map(([title, value, sub, Icon]) => (
          <div className="stat" key={title}>
            <span>
              {title}
              <Icon size={17} />
            </span>
            <strong>{value}</strong>
            <small>{sub}</small>
          </div>
        ))}
      </div>
      <div className="overview-grid">
        <section className="panel launch-panel">
          <div className="section-top">
            <div>
              <span className="eyebrow">READY WHEN YOU ARE</span>
              <h2>
                {complete === 4
                  ? "Your workspace is ready."
                  : "Build your application engine."}
              </h2>
            </div>
            <span className="fraction">
              {complete}
              <small>/ 4</small>
            </span>
          </div>
          <p>
            A few things to set up. Then let your workers handle the repeat
            work.
          </p>
          <div className="setup-steps">
            {steps.map((s, i) => (
              <button
                key={s.title}
                onClick={() => go(s.page)}
                className={s.ok ? "done" : ""}
              >
                <span className="step-number">
                  {s.ok ? <Check size={16} /> : i + 1}
                </span>
                <span>
                  <strong>{s.title}</strong>
                  <small>{s.text}</small>
                </span>
                <ArrowRight size={17} />
              </button>
            ))}
          </div>
        </section>
        <section className="demo-panel">
          <span className="outline-icon">
            <Sparkles size={27} />
          </span>
          <span className="eyebrow">SEE THE WHOLE FLOW</span>
          <h2>
            Meet your
            <br />
            application worker.
          </h2>
          <p>
            Watch a real browser fill a local test form, attach a sample resume,
            and capture a receipt.
          </p>
          <div className="demo-points">
            <span>
              <Check size={14} /> No API keys needed
            </span>
            <span>
              <Check size={14} /> Fictional applicant, local destination
            </span>
          </div>
          <button
            className="button light"
            disabled={!data.config.demo || !data.config.workers_enabled}
            onClick={() =>
              act(
                () => api("/demo", "POST", { mode: "submit" }),
                "Demo queued. Watch its run below.",
              )
            }
          >
            <Play size={15} /> Run the local demo <ArrowRight size={16} />
          </button>
        </section>
      </div>
      <div className="lower-grid">
        <section className="panel">
          <div className="section-top">
            <h2>Recent activity</h2>
            <button className="text-button" onClick={() => go("Applications")}>
              View all <ArrowRight size={15} />
            </button>
          </div>
          {data.runs.length ? (
            <div className="activity-list">
              {data.runs.slice(0, 5).map((run) => {
                const j = data.jobs.find((j) => j.id === run.job_id);
                return (
                  <button key={run.id} onClick={() => inspect(run)}>
                    <span className="company-icon">
                      {short(j?.company || "Job")}
                    </span>
                    <span>
                      <strong>{j?.title || "Application"}</strong>
                      <small>
                        {j?.company} · {when(run.created_at)}
                      </small>
                    </span>
                    <Badge value={run.state} />
                    <ChevronRight size={16} />
                  </button>
                );
              })}
            </div>
          ) : (
            <Empty title="Your next move goes here">
              Applications and receipts appear here as your workers get started.
            </Empty>
          )}
        </section>
        <section className="panel connections">
          <div className="section-top">
            <h2>Connections</h2>
            <SlidersHorizontal size={17} />
          </div>
          {[
            ["MiMo", data.config.mimo, "Application reasoning"],
            ["Telegram", data.config.telegram, "Updates and reviews"],
            ["CapSolver", data.config.capsolver, "Optional captcha assistance"],
          ].map(([name, ok, desc]) => (
            <div className="connection" key={String(name)}>
              <span className="connection-icon">{String(name)[0]}</span>
              <span>
                <strong>{name}</strong>
                <small>{desc}</small>
              </span>
              <span className={"status-text " + (ok ? "green" : "")}>
                {ok ? "Connected" : "Not set"}
              </span>
            </div>
          ))}
          <button className="text-button" onClick={() => go("Settings")}>
            Manage integrations <ArrowRight size={15} />
          </button>
        </section>
      </div>
      <section className="ats-strip">
        <span>BUILT FOR COMMON ATS FLOWS</span>
        <div>
          {[
            "Greenhouse",
            "Workday",
            "Oracle",
            "iCIMS",
            "Ashby",
            "SmartRecruiters",
          ].map((x) => (
            <strong key={x}>{x}</strong>
          ))}
        </div>
        <small>
          Browser adapters enabled. Employer-specific live validation is still
          required.
        </small>
      </section>
    </>
  );
}

function Applications({
  data,
  act,
  inspect,
  busy,
}: {
  data: Snapshot;
  act: Action;
  inspect: (r: Run) => void;
  busy: boolean;
}) {
  const [query, setQuery] = useState(""),
    [filter, setFilter] = useState("all"),
    [importing, setImporting] = useState(false),
    [selected, setSelected] = useState<Job | null>(null);
  const [url, setUrl] = useState(""),
    [title, setTitle] = useState(""),
    [company, setCompany] = useState(""),
    [resume, setResume] = useState(""),
    [reason, setReason] = useState("");
  const jobs = data.jobs.filter(
    (j) =>
      (filter === "all" ||
        (filter === "review"
          ? [
              "review",
              "needs_review",
              "submission_unknown",
              "timed_out",
              "failed",
            ].includes(j.status)
          : filter === "active"
            ? active.includes(j.status)
            : j.status === filter)) &&
      `${j.title} ${j.company} ${j.ats}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const current =
    (selected && data.jobs.find((j) => j.id === selected.id)) || selected;
  return (
    <>
      <div className="toolbar">
        <div className="tabs">
          {[
            ["all", "All applications"],
            ["ready", "Ready"],
            ["active", "In motion"],
            ["review", "Needs attention"],
            ["confirmed", "Confirmed"],
          ].map(([v, t]) => (
            <button
              key={v}
              className={filter === v ? "selected" : ""}
              onClick={() => setFilter(v)}
            >
              {t}
            </button>
          ))}
        </div>
        <button
          className="button primary compact"
          onClick={() => setImporting(!importing)}
        >
          <Plus size={16} /> Import job
        </button>
      </div>
      {importing && (
        <form
          className="panel inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            act(async () => {
              await api("/jobs", "POST", { url, title, company });
              setImporting(false);
              setUrl("");
              setTitle("");
              setCompany("");
            }, "Job added. Classify it to choose a resume.");
          }}
        >
          <Field title="Job URL">
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              required
              type="url"
              placeholder="https://jobs…"
            />
          </Field>
          <Field title="Role (optional)">
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Software Engineer"
            />
          </Field>
          <Field title="Company (optional)">
            <input
              value={company}
              onChange={(e) => setCompany(e.target.value)}
              placeholder="Company name"
            />
          </Field>
          <button className="button primary" disabled={busy}>
            Add job
          </button>
        </form>
      )}
      <section className="panel jobs-panel">
        <div className="search-row">
          <div className="search">
            <Search size={17} />
            <input
              aria-label="Search jobs"
              placeholder="Search role, company, or ATS…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <small>{jobs.length} opportunities</small>
        </div>
        {jobs.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>OPPORTUNITY</th>
                  <th>FIT</th>
                  <th>PLATFORM</th>
                  <th>STATUS</th>
                  <th>ADDED</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {jobs.map((j) => (
                  <tr
                    key={j.id}
                    onClick={() => {
                      setSelected(j);
                      setResume(j.resume_id || data.resumes[0]?.id || "");
                      setReason("");
                    }}
                  >
                    <td>
                      <div className="job-cell">
                        <span className="company-icon">{short(j.company)}</span>
                        <span>
                          <strong>{j.title || "Untitled opportunity"}</strong>
                          <small>
                            {j.company || "Company pending"}
                            {j.location ? " · " + j.location : ""}
                            {j.demo ? " · Demo" : ""}
                          </small>
                        </span>
                      </div>
                    </td>
                    <td>
                      <span
                        className={
                          "score " + ((j.score || 0) >= 70 ? "high" : "")
                        }
                      >
                        {j.score === null ? "—" : j.score + "%"}
                      </span>
                    </td>
                    <td>
                      <span className="ats-label">{j.ats}</span>
                    </td>
                    <td>
                      <Badge value={j.status} />
                    </td>
                    <td className="muted nowrap">{when(j.created_at)}</td>
                    <td>
                      <ChevronRight size={16} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title={
              query
                ? "No matching opportunities"
                : "Make room for your next role"
            }
          >
            Paste a job URL, or connect a company board in Sources to start
            discovering roles.
          </Empty>
        )}
      </section>
      {current && (
        <div className="overlay" onClick={() => setSelected(null)}>
          <aside className="drawer" onClick={(e) => e.stopPropagation()}>
            <button
              className="close"
              aria-label="Close application"
              onClick={() => setSelected(null)}
            >
              <X />
            </button>
            <span className="eyebrow">APPLICATION DETAILS</span>
            <h2>{current.title || "Untitled opportunity"}</h2>
            <p>
              {current.company} · {current.location || "Location not listed"}
            </p>
            <Badge value={current.status} />
            <a
              className="external"
              href={current.url}
              target="_blank"
              rel="noreferrer"
            >
              View original listing <SquareArrowOutUpRight size={14} />
            </a>
            <div className="note">
              <strong>Match assessment</strong>
              <p>
                {current.reason ||
                  "Classify this job against your profile and resume library."}
              </p>
            </div>
            <button
              className="button"
              disabled={busy || current.demo || active.includes(current.status)}
              onClick={() =>
                act(
                  () => api(`/jobs/${current.id}/rank`, "POST"),
                  "Classification complete",
                )
              }
            >
              <Sparkles size={16} /> Classify & select resume
            </button>
            <form
              className="subsection"
              onSubmit={(e) => {
                e.preventDefault();
                act(
                  () =>
                    api(`/jobs/${current.id}/approve`, "POST", {
                      resume_id: resume,
                      reason,
                    }),
                  "Job approved for submission",
                );
              }}
            >
              <h3>Applicant review</h3>
              <Field title="Ready-to-send resume">
                <select
                  value={resume}
                  onChange={(e) => setResume(e.target.value)}
                  required
                >
                  <option value="">Select a resume</option>
                  {data.resumes.map((r) => (
                    <option value={r.id} key={r.id}>
                      {r.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field title="Why is this role a fit?">
                <textarea
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  minLength={5}
                  required
                  placeholder="Confirm location, eligibility, and experience match…"
                />
              </Field>
              <button className="button" disabled={busy || current.demo}>
                Approve match
              </button>
            </form>
            <div className="subsection">
              <h3>Launch an application</h3>
              <p className="muted">
                A dry run fills and verifies the form. Submission requires an
                approved match and the submission switch in Settings.
              </p>
              <div className="button-row">
                <button
                  className="button"
                  disabled={busy || active.includes(current.status)}
                  onClick={() =>
                    act(
                      () =>
                        api(`/jobs/${current.id}/queue`, "POST", {
                          mode: "dry_run",
                          resume_id: resume || undefined,
                        }),
                      "Dry run queued",
                    )
                  }
                >
                  <Play size={15} /> Dry run
                </button>
                <button
                  className="button primary"
                  disabled={
                    busy ||
                    current.status !== "ready" ||
                    !data.control.auto_submit
                  }
                  onClick={() =>
                    act(
                      () =>
                        api(`/jobs/${current.id}/queue`, "POST", {
                          mode: "submit",
                          resume_id: resume || undefined,
                        }),
                      "Application queued",
                    )
                  }
                >
                  <Send size={15} /> Apply
                </button>
              </div>
            </div>
            <div className="subsection">
              <h3>Run history</h3>
              {data.runs
                .filter((r) => r.job_id === current.id)
                .map((r) => (
                  <button
                    className="run-item"
                    key={r.id}
                    onClick={() => {
                      setSelected(null);
                      inspect(r);
                    }}
                  >
                    <span>
                      <strong>{label(r.mode)}</strong>
                      <small>{when(r.created_at)}</small>
                    </span>
                    <Badge value={r.state} />
                    <ChevronRight size={16} />
                  </button>
                ))}
            </div>
          </aside>
        </div>
      )}
    </>
  );
}

function Reviews({
  data,
  act,
  go,
}: {
  data: Snapshot;
  act: Action;
  go: (s: string) => void;
}) {
  return (
    <>
      {data.reviews.length ? (
        <div className="review-grid">
          {data.reviews.map((q) => (
            <ReviewCard
              q={q}
              key={q.id}
              act={act}
              company={
                data.jobs.find(
                  (j) =>
                    j.id === data.runs.find((r) => r.id === q.run_id)?.job_id,
                )?.company || "Application"
              }
            />
          ))}
        </div>
      ) : (
        <section className="panel">
          <Empty title="Your review inbox is clear">
            Questions with missing evidence appear here. Your workers won’t
            guess an answer.
          </Empty>
          <div className="center-action">
            <button className="button" onClick={() => go("Knowledge base")}>
              Improve your knowledge base <ArrowRight size={16} />
            </button>
          </div>
        </section>
      )}
      <div className="note">
        <ShieldCheck size={21} />
        <span>
          <strong>Every answer has a scope.</strong>
          <p>
            An approved answer is reused only for the same employer, question,
            section, and options. After resolving all reviews, approve and
            requeue the job in Applications. Authentication reviews also need an
            imported session.
          </p>
        </span>
      </div>
    </>
  );
}
function ReviewCard({
  q,
  act,
  company,
}: {
  q: Review;
  act: Action;
  company: string;
}) {
  const [answer, setAnswer] = useState("");
  return (
    <form
      className="panel review-card"
      onSubmit={(e) => {
        e.preventDefault();
        act(
          () => api("/reviews/" + q.id, "POST", { answer }),
          "Answer saved. You can requeue the application.",
        );
      }}
    >
      <span className="eyebrow">{company}</span>
      <h3>{q.question}</h3>
      <p>{q.reason}</p>
      <Field
        title={q.key === "session" ? "Resolution note" : "Your verified answer"}
      >
        {q.options.length ? (
          <select
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            required
          >
            <option value="">Choose an option</option>
            {q.options.map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        ) : (
          <textarea
            required
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            placeholder="Add the exact answer or resolution note…"
          />
        )}
      </Field>
      <button className="button primary">
        Save answer <Check size={16} />
      </button>
    </form>
  );
}

function Resumes({ data, act }: { data: Snapshot; act: Action }) {
  const [file, setFile] = useState<File | null>(null),
    [name, setName] = useState(""),
    [roles, setRoles] = useState("");
  return (
    <div className="split-grid">
      <section>
        <div className="section-top">
          <h2>Your resume library</h2>
          <span className="muted">{data.resumes.length} / 10 variants</span>
        </div>
        {data.resumes.length ? (
          <div className="resume-grid">
            {data.resumes.map((r) => (
              <article className="panel resume-card" key={r.id}>
                <div className="resume-art">
                  <FileText size={35} />
                  <span>PDF</span>
                  <i />
                  <i />
                  <i />
                </div>
                <div className="resume-body">
                  <h3>{r.name}</h3>
                  <div className="tags">
                    {r.roles.map((t) => (
                      <span key={t}>{t}</span>
                    ))}
                  </div>
                  <small>Added {when(r.created_at)}</small>
                  <div className="resume-actions">
                    <a
                      href={`/api/resumes/${r.id}/download`}
                      className="text-button"
                    >
                      <ArrowDownToLine size={15} /> Download
                    </a>
                    <button
                      className="text-button danger"
                      onClick={() =>
                        act(
                          () => api("/resumes/" + r.id, "DELETE"),
                          "Removed from the library",
                        )
                      }
                    >
                      Remove
                    </button>
                  </div>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="panel">
            <Empty title="Good resumes, ready to go">
              Upload your role-specific PDFs. The classifier selects an existing
              file without rewriting it.
            </Empty>
          </div>
        )}
      </section>
      <form
        className="panel upload-panel"
        onSubmit={(e) => {
          e.preventDefault();
          if (!file) return;
          const f = new FormData();
          f.append("file", file);
          f.append("name", name);
          f.append("roles", roles);
          act(async () => {
            await api("/resumes", "POST", f);
            setFile(null);
            setName("");
            setRoles("");
          }, "Resume uploaded and indexed");
        }}
      >
        <h2>Add a resume</h2>
        <label className="dropzone">
          <Upload size={28} />
          <strong>{file ? file.name : "Choose a PDF"}</strong>
          <small>Text-based PDF · up to 5 MB</small>
          <input
            aria-label="Resume PDF"
            type="file"
            accept="application/pdf"
            onChange={(e) => {
              const f = e.target.files?.[0] || null;
              setFile(f);
              if (f && !name) setName(f.name.replace(/\.pdf$/i, ""));
            }}
          />
        </label>
        <Field title="Resume name">
          <input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Backend engineering"
          />
        </Field>
        <Field title="Role tags" hint="Separate roles with commas.">
          <input
            value={roles}
            onChange={(e) => setRoles(e.target.value)}
            placeholder="Backend, Python, Platform"
          />
        </Field>
        <button
          className="button primary"
          disabled={!file || data.resumes.length >= 10}
        >
          <Plus size={16} /> Add to library
        </button>
        <p className="muted small">
          The exact PDF is attached. Its checksum is recorded with the
          application receipt.
        </p>
      </form>
    </div>
  );
}

function Knowledge({ initial, act }: { initial: Profile; act: Action }) {
  const [p, setP] = useState(initial),
    [facts, setFacts] = useState(JSON.stringify(initial.facts, null, 2)),
    [evidence, setEvidence] = useState(
      initial.evidence.map((e) => e.text).join("\n\n---\n\n"),
    );
  const set = (k: keyof Profile, v: unknown) => setP((x) => ({ ...x, [k]: v }));
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        act(async () => {
          let f: unknown;
          try {
            f = JSON.parse(facts);
          } catch {
            throw new Error("Verified facts must be valid JSON");
          }
          if (!f || Array.isArray(f) || typeof f !== "object")
            throw new Error("Verified facts must be a JSON object");
          await api("/profile", "PUT", {
            ...p,
            facts: f,
            evidence: evidence
              .split(/\n\s*---\s*\n/)
              .filter((x) => x.trim())
              .map((text, i) => ({ id: "note-" + (i + 1), text: text.trim() })),
          });
        }, "Knowledge base saved");
      }}
    >
      <div className="knowledge-grid">
        <section className="panel form-panel">
          <div className="section-top">
            <h2>Personal details</h2>
            <UserRound size={19} />
          </div>
          <div className="form-grid">
            {[
              ["name", "Full legal name"],
              ["email", "Email address"],
              ["phone", "Phone number"],
              ["location", "Current location"],
              ["linkedin", "LinkedIn URL"],
              ["website", "Portfolio / website"],
            ].map(([k, t]) => (
              <Field key={k} title={t}>
                <input
                  type={k === "email" ? "email" : "text"}
                  value={String(p[k as keyof Profile] || "")}
                  onChange={(e) => set(k as keyof Profile, e.target.value)}
                />
              </Field>
            ))}
          </div>
          <div className="section-top subsection">
            <h2>Opportunity preferences</h2>
          </div>
          <Field title="Target roles">
            <input
              value={p.target_roles.join(", ")}
              onChange={(e) =>
                set(
                  "target_roles",
                  e.target.value.split(",").map((x) => x.trim()),
                )
              }
              placeholder="Software Engineer, Data Engineer"
            />
          </Field>
          <Field title="Target locations">
            <input
              value={p.target_locations.join(", ")}
              onChange={(e) =>
                set(
                  "target_locations",
                  e.target.value.split(",").map((x) => x.trim()),
                )
              }
              placeholder="New York, Remote US"
            />
          </Field>
          <div className="form-grid">
            <Field title="Exclude title keywords">
              <input
                value={p.excluded_keywords.join(", ")}
                onChange={(e) =>
                  set(
                    "excluded_keywords",
                    e.target.value.split(",").map((x) => x.trim()),
                  )
                }
              />
            </Field>
            <Field title="Minimum match score">
              <input
                type="number"
                min="0"
                max="100"
                value={p.minimum_fit}
                onChange={(e) => set("minimum_fit", Number(e.target.value))}
              />
            </Field>
          </div>
          <label className="checkline">
            <input
              type="checkbox"
              checked={p.decline_demographics}
              onChange={(e) => set("decline_demographics", e.target.checked)}
            />
            <span>
              Choose “prefer not to identify” on demographic questions
            </span>
          </label>
        </section>
        <section className="panel form-panel">
          <div className="section-top">
            <h2>Verified facts</h2>
            <ShieldCheck size={19} />
          </div>
          <p className="muted">
            Add explicit facts with clear time and country scope. First and last
            names must be separate verified facts; the worker will not guess
            them.
          </p>
          <Field title="Structured facts (JSON)">
            <textarea
              className="code-input facts"
              spellCheck={false}
              value={facts}
              onChange={(e) => setFacts(e.target.value)}
            />
          </Field>
          <details>
            <summary>Example structure</summary>
            <pre>
              {
                '{\n  "first_name": "Your first name",\n  "last_name": "Your last name",\n  "work_authorization_US": "Your actual status",\n  "requires_future_sponsorship_US": true,\n  "conviction_history": "Your accurate history and scope",\n  "government_employment_history": "Include dates and public institutions"\n}'
              }
            </pre>
          </details>
          <div className="note small">
            <span>
              Answer disclosure questions from facts. “No convictions” does not
              imply “No” to a promise to follow anti-corruption policies.
            </span>
          </div>
          <Field
            title="Approved consent statements"
            hint="One exact checkbox label per line. Only list commitments you accept."
          >
            <textarea
              value={p.approved_consents.join("\n")}
              onChange={(e) =>
                set(
                  "approved_consents",
                  e.target.value.split("\n").filter(Boolean),
                )
              }
            />
          </Field>
          <small className="muted">
            {Object.keys(p.approved_answers).length} exact answers saved from
            your review inbox.
          </small>
        </section>
      </div>
      <section className="panel form-panel subsection">
        <div className="section-top">
          <div>
            <h2>Experience & answer evidence</h2>
            <p className="muted">
              Projects, achievements, education, and clear examples your worker
              can cite. Separate notes with a line containing three dashes
              (---).
            </p>
          </div>
          <FileText size={21} />
        </div>
        <textarea
          className="evidence-input"
          value={evidence}
          onChange={(e) => setEvidence(e.target.value)}
          placeholder={
            "Project: …\nWhat I did: …\nTechnologies: …\nResult: …\n\n---\n\nEducation and dates: …"
          }
        />
      </section>
      <div className="save-bar">
        <span>
          <ShieldCheck size={16} /> Only facts you save can support application
          answers.
        </span>
        <button className="button primary">
          <Check size={16} /> Save knowledge base
        </button>
      </div>
    </form>
  );
}

function Sources({ data, act }: { data: Snapshot; act: Action }) {
  const [name, setName] = useState(""),
    [url, setUrl] = useState(""),
    [kind, setKind] = useState("greenhouse"),
    [cron, setCron] = useState("0 */3 * * *"),
    [auto, setAuto] = useState(false);
  const toggle = (s: Source) =>
    act(() =>
      api("/sources/" + s.id, "PUT", {
        name: s.name,
        kind: s.kind,
        url: s.url,
        cron: s.cron,
        enabled: !s.enabled,
        auto_queue: s.auto_queue,
      }),
    );
  return (
    <div className="split-grid">
      <section>
        <div className="section-top">
          <h2>Connected job boards</h2>
          <span className="muted">Schedules use {data.config.timezone}</span>
        </div>
        {data.sources.length ? (
          <div className="source-list">
            {data.sources.map((s) => (
              <article className="panel source-card" key={s.id}>
                <div className="section-top">
                  <div className="job-cell">
                    <span className="company-icon">{short(s.name)}</span>
                    <span>
                      <h3>{s.name}</h3>
                      <small>
                        {s.kind} · {s.enabled ? "Scheduled" : "Paused"}
                      </small>
                    </span>
                  </div>
                  <button
                    className="icon-button"
                    title={s.enabled ? "Pause source" : "Enable source"}
                    onClick={() => toggle(s)}
                  >
                    {s.enabled ? <Pause size={17} /> : <Play size={17} />}
                  </button>
                </div>
                <a
                  href={s.url}
                  className="source-url"
                  target="_blank"
                  rel="noreferrer"
                >
                  {s.url} <SquareArrowOutUpRight size={13} />
                </a>
                <div className="source-meta">
                  <span>
                    <Clock3 size={14} />
                    {s.cron}
                  </span>
                  <span>{s.found} new last run</span>
                  <span>
                    {s.auto_queue ? "Classify & queue" : "Discover only"}
                  </span>
                </div>
                {s.error && <p className="error-text">{s.error}</p>}
                <div className="source-footer">
                  <small>Last run: {when(s.last_run)}</small>
                  <button
                    className="text-button"
                    onClick={() =>
                      act(
                        () => api(`/sources/${s.id}/discover`, "POST"),
                        "Discovery queued",
                      )
                    }
                  >
                    <RefreshCw size={14} /> Discover now
                  </button>
                  <button
                    className="text-button danger"
                    onClick={() => act(() => api("/sources/" + s.id, "DELETE"))}
                  >
                    Remove
                  </button>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="panel">
            <Empty title="A steady stream of better matches">
              Connect company boards you care about. Each source has its own
              schedule.
            </Empty>
          </div>
        )}
      </section>
      <form
        className="panel form-panel"
        onSubmit={(e) => {
          e.preventDefault();
          act(async () => {
            await api("/sources", "POST", {
              name,
              url,
              kind,
              cron,
              enabled: true,
              auto_queue: auto,
            });
            setName("");
            setUrl("");
          }, "Source connected");
        }}
      >
        <h2>Connect a source</h2>
        <Field title="Company / source name">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            placeholder="Company name"
          />
        </Field>
        <Field title="Board type">
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {[
              "greenhouse",
              "lever",
              "ashby",
              "smartrecruiters",
              "workday",
              "career_page",
            ].map((k) => (
              <option key={k} value={k}>
                {label(k)}
              </option>
            ))}
          </select>
        </Field>
        <Field
          title="Company board URL"
          hint="Use the company’s board homepage, including its slug, not a single job."
        >
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            required
            placeholder="https://boards.greenhouse.io/company"
          />
        </Field>
        <Field
          title="Schedule (cron)"
          hint="0 */3 * * * = every three hours. The scheduler checks once a minute."
        >
          <input
            className="code-input"
            value={cron}
            onChange={(e) => setCron(e.target.value)}
            required
          />
        </Field>
        <label className="checkline">
          <input
            type="checkbox"
            checked={auto}
            onChange={(e) => setAuto(e.target.checked)}
          />
          <span>Classify new jobs and queue eligible matches</span>
        </label>
        <p className="muted small">
          Auto-queue also requires MiMo, a resume, verified profile facts, and
          the submission switch in Settings.
        </p>
        <button className="button primary">
          <Plus size={16} /> Connect source
        </button>
      </form>
    </div>
  );
}

function Settings({ data, act }: { data: Snapshot; act: Action }) {
  const [sessionUrl, setSessionUrl] = useState(""),
    [file, setFile] = useState<File | null>(null);
  return (
    <>
      <div className="knowledge-grid">
        <section className="panel form-panel">
          <div className="section-top">
            <h2>Operating controls</h2>
            <SlidersHorizontal size={20} />
          </div>
          <div className="setting-row">
            <div>
              <strong>Automatic submission</strong>
              <p>Allow approved jobs to be submitted after verification.</p>
            </div>
            <button
              aria-label="Toggle automatic submission"
              aria-pressed={data.control.auto_submit}
              className={"switch " + (data.control.auto_submit ? "on" : "")}
              onClick={() =>
                act(() =>
                  api("/control", "PUT", {
                    ...data.control,
                    auto_submit: !data.control.auto_submit,
                  }),
                )
              }
            >
              <i />
            </button>
          </div>
          <div className="setting-row">
            <div>
              <strong>Pause workers</strong>
              <p>Stop new work and block active runs before the commit step.</p>
            </div>
            <button
              aria-label="Toggle pause"
              aria-pressed={data.control.paused}
              className={"switch " + (data.control.paused ? "on" : "")}
              onClick={() =>
                act(() =>
                  api("/control", "PUT", {
                    ...data.control,
                    paused: !data.control.paused,
                  }),
                )
              }
            >
              <i />
            </button>
          </div>
          <div className="limit-grid">
            {[
              ["Workers", data.config.workers],
              ["Per application", data.config.timeout + " seconds"],
              ["Daily submission cap", data.config.daily_limit],
              ["Daily model budget", money(data.config.budget)],
            ].map(([k, v]) => (
              <div key={String(k)}>
                <small>{k}</small>
                <strong>{v}</strong>
              </div>
            ))}
          </div>
          <p className="muted small">
            Change limits in .env and restart. Attempts with uncertain receipts
            reserve a submission slot and are never retried automatically.
          </p>
        </section>
        <section className="panel form-panel">
          <div className="section-top">
            <h2>Provider connections</h2>
            <Zap size={20} />
          </div>
          {[
            ["MiMo", data.config.mimo, "MIMO_API_KEY", data.config.model],
            [
              "Telegram",
              data.config.telegram,
              "TELEGRAM_BOT_TOKEN + TELEGRAM_USER_ID",
              "Direct messages from your allowlisted numeric user ID",
            ],
            [
              "CapSolver",
              data.config.capsolver,
              "CAPSOLVER_API_KEY",
              "One attempt per run on supported challenges",
            ],
          ].map(([name, ok, key, desc]) => (
            <div className="provider" key={String(name)}>
              <div>
                <strong>{name}</strong>
                <Badge value={ok ? "ready" : "not configured"} />
              </div>
              <p>{desc}</p>
              <code>{key}</code>
            </div>
          ))}
          <div className="note small">
            <span>
              Put keys in the server’s .env file, then restart. Keys are never
              sent to this dashboard.
            </span>
          </div>
        </section>
      </div>
      <section className="panel form-panel subsection">
        <div className="section-top">
          <div>
            <h2>Employer sessions</h2>
            <p className="muted">
              For Workday, Oracle, and other account-based applications, sign in
              once on your own machine.
            </p>
          </div>
          <UserRound size={21} />
        </div>
        <p>
          Run <code>jobpilot login https://employer.careers.example</code>{" "}
          locally to save a session. For a Docker deployment, import the
          Playwright storage-state JSON below.
        </p>
        <form
          className="inline-form plain"
          onSubmit={(e) => {
            e.preventDefault();
            if (!file) return;
            const f = new FormData();
            f.append("url", sessionUrl);
            f.append("file", file);
            act(() => api("/sessions", "POST", f), "Employer session imported");
          }}
        >
          <Field title="Exact employer URL">
            <input
              type="url"
              required
              value={sessionUrl}
              onChange={(e) => setSessionUrl(e.target.value)}
            />
          </Field>
          <Field title="Storage-state JSON">
            <input
              type="file"
              accept="application/json"
              required
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
          </Field>
          <button className="button" disabled={!file}>
            <Upload size={16} /> Import session
          </button>
        </form>
        <small className="muted">
          Expired sessions, email verification, OTPs, and unsupported widgets
          are sent to review. Session files remain in your private data
          directory.
        </small>
      </section>
      <section className="panel form-panel subsection">
        <div className="section-top">
          <h2>ATS adapters</h2>
          <span className="muted">Live validation pending</span>
        </div>
        <div className="adapter-grid">
          {data.ats.map((a) => (
            <div key={a.id}>
              <strong>{a.name}</strong>
              <small>{a.guidance}</small>
            </div>
          ))}
        </div>
      </section>
      <button
        className="button subsection"
        onClick={() =>
          act(() =>
            api("/logout", "POST").then(() =>
              window.dispatchEvent(new Event("signed-out")),
            ),
          )
        }
      >
        <LogOut size={16} /> Sign out
      </button>
    </>
  );
}

function RunDrawer({
  detail,
  data,
  close,
  act,
  reload,
}: {
  detail: Detail;
  data: Snapshot;
  close: () => void;
  act: Action;
  reload: () => Promise<unknown>;
}) {
  const r = detail.run,
    j = data.jobs.find((j) => j.id === r.job_id);
  const [evidence, setEvidence] = useState(""),
    [submitted, setSubmitted] = useState(true);
  return (
    <div className="overlay" onClick={close}>
      <aside className="drawer wide" onClick={(e) => e.stopPropagation()}>
        <button className="close" aria-label="Close run" onClick={close}>
          <X />
        </button>
        <span className="eyebrow">RUN RECEIPT · {r.id.slice(0, 8)}</span>
        <h2>{j?.title || "Application"}</h2>
        <p>
          {j?.company} · {label(r.mode)} {j?.demo ? "· Local demo" : ""}
        </p>
        <div className="section-top">
          <Badge value={r.state} />
          <button className="text-button" onClick={() => act(reload)}>
            <RefreshCw size={14} /> Refresh run
          </button>
        </div>
        <div className="limit-grid">
          <div>
            <small>Elapsed</small>
            <strong>{r.elapsed.toFixed(1)}s</strong>
          </div>
          <div>
            <small>Model spend</small>
            <strong>{money(r.cost)}</strong>
          </div>
          <div>
            <small>Model calls</small>
            <strong>{r.model_calls}</strong>
          </div>
        </div>
        <p>{r.reason}</p>
        {r.state === "queued" && (
          <button
            className="button"
            onClick={() =>
              act(async () => {
                await api("/runs/" + r.id + "/cancel", "POST");
                await reload();
              })
            }
          >
            Cancel queued run
          </button>
        )}
        {r.state === "submission_unknown" && (
          <form
            className="note reconcile"
            onSubmit={(e) => {
              e.preventDefault();
              act(async () => {
                await api("/runs/" + r.id + "/reconcile", "POST", {
                  submitted,
                  evidence,
                });
                await reload();
              }, "Submission reconciled");
            }}
          >
            <h3>Resolve the submission outcome</h3>
            <p>
              Check the employer portal or confirmation email before permitting
              another attempt.
            </p>
            <Field title="Verified outcome">
              <select
                value={String(submitted)}
                onChange={(e) => setSubmitted(e.target.value === "true")}
              >
                <option value="true">Application was submitted</option>
                <option value="false">Application was not submitted</option>
              </select>
            </Field>
            <Field title="Evidence / explanation">
              <textarea
                required
                minLength={5}
                value={evidence}
                onChange={(e) => setEvidence(e.target.value)}
              />
            </Field>
            <button className="button primary">Save verified outcome</button>
          </form>
        )}
        {Object.keys(r.receipt).length > 0 && (
          <div className="note">
            <div>
              <strong>Submission evidence</strong>
              <pre>{JSON.stringify(r.receipt, null, 2)}</pre>
            </div>
          </div>
        )}
        <div className="subsection">
          <h3>Answer verification</h3>
          {Object.values(detail.answers).length ? (
            Object.values(detail.answers).map((a, i) => (
              <div className="answer-row" key={i}>
                <span>
                  {a.verified ? (
                    <CheckCircle2 size={16} />
                  ) : (
                    <Circle size={16} />
                  )}
                </span>
                <div>
                  <strong>{a.label}</strong>
                  <p>{a.answer}</p>
                  <small>{a.evidence_ids.join(" · ")}</small>
                </div>
              </div>
            ))
          ) : (
            <p className="muted">
              Answer evidence appears after the browser finishes.
            </p>
          )}
        </div>
        {detail.screenshot && (
          <div className="subsection">
            <h3>Final browser state</h3>
            <a
              href={`/api/runs/${r.id}/screenshot`}
              target="_blank"
              rel="noreferrer"
            >
              <img
                className="screenshot"
                src={`/api/runs/${r.id}/screenshot`}
                alt="Final application page captured by the worker"
              />
            </a>
          </div>
        )}
        <div className="subsection">
          <h3>Event timeline</h3>
          <div className="timeline">
            {detail.events.map((e) => (
              <div key={e.id}>
                <i />
                <small>
                  {when(e.created_at)} · {label(e.kind)}
                </small>
                <p>{e.message}</p>
              </div>
            ))}
          </div>
        </div>
      </aside>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
