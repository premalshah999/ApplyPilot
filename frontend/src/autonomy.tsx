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
  AccountRow,
  AccountSummary,
  Profile,
  ReviewedAnswer,
  Snapshot,
} from "./types";

type Request = <T = any>(
  path: string,
  method?: string,
  body?: unknown,
) => Promise<T>;
type Action = (fn: () => Promise<unknown>, success?: string) => Promise<void>;

const when = (s: string | number) =>
  new Date(s).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

const ACCOUNT_STATES: Record<string, string> = {
  created_locally: "Created, not signed in yet",
  signing_in: "Signing in",
  authenticated: "Signed in",
  verification_pending: "Waiting for email verification",
  reset_requested: "Password reset requested",
  password_reset: "Password reset",
  exists: "Already registered",
  locked: "Locked",
};
const accountState = (s: string) =>
  ACCOUNT_STATES[s] || s.replace(/_/g, " ") || "Unknown";
const accountHost = (a: AccountRow) =>
  a.origin.replace(/^https?:\/\//, "") ||
  a.id.replace(/^employer_account:/, "");

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
            One login for every portal: your login email with
            APPLICATION_PASSWORD (ACCOUNT_PASSWORD is still accepted). If it is
            unset, one strong password is generated once, encrypted, and reused
            for every employer. Verification and reset emails are read from your
            Gmail inbox.
          </p>
        </div>
        <KeyRound size={21} />
      </div>
      <div className="limit-grid">
        <div>
          <small>Login email</small>
          <strong>{data.config.account_email || "Profile email"}</strong>
        </div>
        <div>
          <small>APPLICATION_PASSWORD</small>
          <strong>
            {data.config.account ? "Set in .env" : "Generated and encrypted"}
          </strong>
        </div>
        <div>
          <small>Gmail app password</small>
          <strong>{data.config.imap ? "Configured" : "Missing"}</strong>
        </div>
        <div>
          <small>2Captcha</small>
          <strong>{data.config.twocaptcha ? "Configured" : "Not set"}</strong>
        </div>
        <div>
          <small>Engine</small>
          <strong>{data.config.engine}</strong>
        </div>
      </div>
      {!data.profile.allow_account_creation && (
        <div className="note small">
          <span>
            Account creation is off. Turn on “Create and reuse employer
            accounts” in Profile to let applications that require an account
            continue.
          </span>
        </div>
      )}
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
            act(
              () =>
                request<{ connected: boolean; folder: string; host: string }>(
                  "/inbox/check",
                  "POST",
                ),
              "Gmail IMAP connection works",
            )
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
                <th>Employer</th>
                <th>State</th>
                <th>Updated</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {summary.accounts.map((a) => (
                <tr key={a.id}>
                  <td>{accountHost(a)}</td>
                  <td>
                    {accountState(a.state)}
                    {a.reset_requested_at ? (
                      <small className="muted">
                        {" "}
                        · reset requested {when(a.reset_requested_at * 1000)}
                      </small>
                    ) : null}
                  </td>
                  <td>{a.updated_at ? when(a.updated_at) : "—"}</td>
                  <td>
                    <button
                      className="icon-button"
                      type="button"
                      aria-label={"Forget " + accountHost(a)}
                      onClick={() =>
                        act(
                          () =>
                            request(
                              "/accounts/" + encodeURIComponent(a.id),
                              "DELETE",
                            ).then(load),
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
          No employer accounts yet. They appear here after the first application
          that needs one.
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
  const [entries, setEntries] = useState<ReviewedAnswer[]>([]);
  const [q, setQ] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [scope, setScope] = useState<ReviewedAnswer["scope"]>("personal");
  const [employer, setEmployer] = useState("");
  const load = (text = q) =>
    request<{ entries: ReviewedAnswer[] }>(
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
            Answers you gave on Telegram, in the review inbox, or here. Reusable
            answers apply at every employer (employer names in a question are
            matched as a placeholder); employer answers only where you gave
            them.
          </p>
        </div>
        <Brain size={21} />
      </div>
      <form
        className="inline-form plain"
        onSubmit={(e) => {
          e.preventDefault();
          act(async () => {
            await request("/knowledge", "POST", {
              question,
              answer,
              options: [],
              scope,
              employer: scope === "employer" ? employer.trim() : "",
            });
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
        <select
          aria-label="Applies to"
          value={scope}
          onChange={(e) => setScope(e.target.value as ReviewedAnswer["scope"])}
        >
          <option value="personal">All employers</option>
          <option value="employer">One employer</option>
        </select>
        {scope === "employer" && (
          <input
            placeholder="Employer name"
            value={employer}
            onChange={(e) => setEmployer(e.target.value)}
            required
          />
        )}
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
                <th>Applies to</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id}>
                  <td>{e.question}</td>
                  <td>{e.answer}</td>
                  <td>
                    {e.scope === "personal"
                      ? "All employers"
                      : e.employer || "One employer"}
                    <br />
                    <small className="muted">
                      {e.layer === "narrative" ? "Narrative" : "Confirmed fact"}
                    </small>
                  </td>
                  <td>
                    <button
                      className="icon-button"
                      type="button"
                      aria-label={"Forget " + e.question}
                      onClick={() =>
                        act(
                          () =>
                            request(
                              "/knowledge/" + encodeURIComponent(e.id),
                              "DELETE",
                            ).then(() => load()),
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
          {q
            ? "No learned answers match this search."
            : "Nothing learned yet. Unknown questions are asked on Telegram and saved here."}
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
