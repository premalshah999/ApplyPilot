import React, { useEffect, useState } from "react";
import {
  Brain,
  KeyRound,
  Mail,
  Plus,
  RefreshCw,
  Sparkles,
  Trash2,
} from "lucide-react";
import type {
  AccountSummary,
  KnowledgeEntry,
  Profile,
  Snapshot,
} from "./types";

type Request = <T = any>(
  path: string,
  method?: string,
  body?: unknown,
) => Promise<T>;
type Action = (fn: () => Promise<unknown>, success?: string) => Promise<void>;

const when = (s: string) =>
  new Date(s).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

export function AccountSettings({
  data,
  request,
  act,
}: {
  data: Snapshot;
  request: Request;
  act: Action;
}) {
  const [summary, setSummary] = useState<AccountSummary | null>(null);
  const load = () =>
    request<AccountSummary>("/accounts")
      .then(setSummary)
      .catch(() => setSummary(null));
  useEffect(() => {
    load();
  }, []);
  return (
    <section className="panel form-panel subsection">
      <div className="section-top">
        <div>
          <h2>Employer accounts</h2>
          <p className="muted">
            One login for every portal. Accounts are created on first use; an
            old password is reset through your mailbox.
          </p>
        </div>
        <KeyRound size={21} />
      </div>
      <div className="limit-grid">
        <div>
          <small>Login email</small>
          <strong>
            {summary?.email || data.config.account_email || "Profile email"}
          </strong>
        </div>
        <div>
          <small>ACCOUNT_PASSWORD</small>
          <strong>{data.config.account ? "Configured" : "Missing"}</strong>
        </div>
        <div>
          <small>Gmail app password</small>
          <strong>{data.config.imap ? "Configured" : "Missing"}</strong>
        </div>
        <div>
          <small>Engine</small>
          <strong>{data.config.engine}</strong>
        </div>
      </div>
      {!!summary?.password_problems.length && (
        <div className="note small">
          <span>
            Password may be rejected by some portals:{" "}
            {summary.password_problems.join("; ")}.
          </span>
        </div>
      )}
      <div className="inline-form plain">
        <button
          className="button"
          type="button"
          disabled={!data.config.imap}
          onClick={() =>
            act(async () => {
              const r = await request<{ folder: string }>(
                "/inbox/check",
                "POST",
              );
              return r;
            }, "Gmail IMAP connection works")
          }
        >
          <Mail size={16} /> Test Gmail connection
        </button>
        <button className="button" type="button" onClick={load}>
          <RefreshCw size={16} /> Refresh
        </button>
      </div>
      {summary && summary.accounts.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Employer realm</th>
                <th>State</th>
                <th>Password</th>
                <th>Updated</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {summary.accounts.map((a) => (
                <tr key={a.id}>
                  <td>{a.realm.replace(/^\w+:/, "")}</td>
                  <td>{a.state}</td>
                  <td>
                    {a.password_current
                      ? "current"
                      : a.resets
                        ? `reset ${a.resets}×`
                        : "unknown"}
                  </td>
                  <td>{when(a.updated_at)}</td>
                  <td>
                    <button
                      className="icon-button"
                      aria-label={"Forget " + a.realm}
                      onClick={() =>
                        act(
                          () =>
                            request("/accounts/" + a.id, "DELETE").then(load),
                          "Account forgotten",
                        )
                      }
                    >
                      <Trash2 size={15} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted small">
          No employer accounts yet. They appear here after the first
          application.
        </p>
      )}
    </section>
  );
}

export function LearnedAnswers({
  request,
  act,
}: {
  request: Request;
  act: Action;
}) {
  const [entries, setEntries] = useState<KnowledgeEntry[]>([]);
  const [q, setQ] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const load = (text = q) =>
    request<{ entries: KnowledgeEntry[] }>(
      "/knowledge?q=" + encodeURIComponent(text),
    )
      .then((r) => setEntries(r.entries))
      .catch(() => setEntries([]));
  useEffect(() => {
    load("");
  }, []);
  return (
    <section className="panel form-panel subsection">
      <div className="section-top">
        <div>
          <h2>Learned answers</h2>
          <p className="muted">
            Answers you gave on Telegram or here. Reused for the same question
            at any employer; employer names are matched as a placeholder.
          </p>
        </div>
        <Brain size={21} />
      </div>
      <form
        className="inline-form plain"
        onSubmit={(e) => {
          e.preventDefault();
          act(async () => {
            await request("/knowledge", "POST", { question, answer });
            setQuestion("");
            setAnswer("");
            await load();
          }, "Answer saved");
        }}
      >
        <input
          placeholder="Question, e.g. Do you have experience with Kafka?"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          required
        />
        <input
          placeholder="Answer"
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          required
        />
        <button className="button">
          <Plus size={16} /> Add
        </button>
      </form>
      <input
        className="search-input"
        placeholder="Search learned answers"
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          load(e.target.value);
        }}
      />
      {entries.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Question</th>
                <th>Answer</th>
                <th>Source</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id}>
                  <td>{e.question}</td>
                  <td>{e.answer}</td>
                  <td>
                    {e.scope === "global"
                      ? e.source
                      : e.source + " · " + e.scope.replace("employer:", "")}
                  </td>
                  <td>
                    <button
                      className="icon-button"
                      aria-label={"Forget " + e.question}
                      onClick={() =>
                        act(
                          () =>
                            request("/knowledge/" + e.id, "DELETE").then(() =>
                              load(),
                            ),
                          "Forgotten",
                        )
                      }
                    >
                      <Trash2 size={15} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted small">
          Nothing learned yet. Unknown questions are asked on Telegram and saved
          here.
        </p>
      )}
    </section>
  );
}

type Setter = (k: keyof Profile, v: unknown) => void;

export function ApplicationProfile({
  p,
  set,
  history,
  setHistory,
  request,
  act,
}: {
  p: Profile;
  set: Setter;
  history: { work: string; education: string };
  setHistory: (h: { work: string; education: string }) => void;
  request: Request;
  act: Action;
}) {
  const addr = p.address;
  const setAddr = (k: keyof Profile["address"], v: string) =>
    set("address", { ...addr, [k]: v });
  const text = (k: keyof Profile, title: string, placeholder = "") => (
    <label className="field" key={String(k)}>
      <span>{title}</span>
      <input
        value={String(p[k] ?? "")}
        placeholder={placeholder}
        onChange={(e) => set(k, e.target.value)}
      />
    </label>
  );
  return (
    <section className="panel form-panel subsection">
      <div className="section-top">
        <div>
          <h2>Application profile</h2>
          <p className="muted">
            Structured details multi-page portals ask for (Workday, Oracle,
            iCIMS, Taleo). Filled directly, without the model.
          </p>
        </div>
        <Sparkles size={21} />
      </div>
      <div className="form-grid">
        {text("first_name", "Legal first name")}
        {text("last_name", "Legal last name")}
        {text("middle_name", "Middle name")}
        {text("preferred_name", "Preferred name")}
        {text("github", "GitHub URL")}
        {text(
          "phone_country_code",
          "Phone country code",
          "+1 or United States of America",
        )}
        {text("phone_type", "Phone type", "Mobile")}
        {text("referral_source", "How did you hear about us", "LinkedIn")}
        {text(
          "salary_expectation",
          "Salary expectation",
          "Leave blank to skip optional salary questions",
        )}
        {text("availability", "Availability / notice", "Two weeks")}
      </div>
      <div className="form-grid">
        {(
          [
            ["line1", "Address line 1"],
            ["line2", "Address line 2"],
            ["city", "City"],
            ["state", "State / province"],
            ["postal_code", "Postal code"],
            ["country", "Country", "United States of America"],
          ] as [keyof Profile["address"], string, string?][]
        ).map(([k, t, ph]) => (
          <label className="field" key={k}>
            <span>{t}</span>
            <input
              value={addr[k] || ""}
              placeholder={ph}
              onChange={(e) => setAddr(k, e.target.value)}
            />
          </label>
        ))}
      </div>
      <label className="checkline">
        <input
          type="checkbox"
          checked={p.auto_accept_consents}
          onChange={(e) => set("auto_accept_consents", e.target.checked)}
        />
        <span>
          Accept standard privacy, terms and accuracy-certification checkboxes
          automatically (never marketing or SMS opt-ins)
        </span>
      </label>
      <div className="section-top subsection">
        <h3>Work history and education</h3>
        <button
          type="button"
          className="button"
          onClick={() =>
            act(async () => {
              const r = await request<any>("/profile/extract", "POST", {});
              setHistory({
                work: JSON.stringify(r.work, null, 2),
                education: JSON.stringify(r.education, null, 2),
              });
              for (const k of ["first_name", "last_name", "github"] as const)
                if (r[k] && !p[k]) set(k, r[k]);
              if (r.skills?.length && !p.skills.length) set("skills", r.skills);
            }, "Extracted from your resume. Review, then save.")
          }
        >
          <Sparkles size={16} /> Extract from resume
        </button>
      </div>
      <p className="muted small">
        JSON lists. Dates as YYYY-MM (or YYYY). Work: company, title, location,
        start, end, current, description. Education: school, degree,
        field_of_study, start, end, gpa.
      </p>
      <div className="form-grid">
        <label className="field">
          <span>Work history (JSON)</span>
          <textarea
            className="code-input facts"
            spellCheck={false}
            value={history.work}
            onChange={(e) => setHistory({ ...history, work: e.target.value })}
          />
        </label>
        <label className="field">
          <span>Education (JSON)</span>
          <textarea
            className="code-input facts"
            spellCheck={false}
            value={history.education}
            onChange={(e) =>
              setHistory({ ...history, education: e.target.value })
            }
          />
        </label>
      </div>
      <label className="field">
        <span>Skills (comma separated)</span>
        <input
          value={p.skills.join(", ")}
          onChange={(e) =>
            set(
              "skills",
              e.target.value
                .split(",")
                .map((x) => x.trim())
                .filter(Boolean),
            )
          }
        />
      </label>
    </section>
  );
}

export function parseHistory(history: { work: string; education: string }) {
  const parse = (t: string, what: string) => {
    if (!t.trim()) return [];
    let v: unknown;
    try {
      v = JSON.parse(t);
    } catch {
      throw new Error(what + " must be valid JSON");
    }
    if (!Array.isArray(v)) throw new Error(what + " must be a JSON list");
    return v;
  };
  return {
    work: parse(history.work, "Work history"),
    education: parse(history.education, "Education"),
  };
}
