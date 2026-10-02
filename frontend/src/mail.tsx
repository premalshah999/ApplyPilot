import { useEffect, useState } from "react";
import { Mail, Plus, RefreshCw, Unplug, X } from "lucide-react";

type Mailbox = { id: string; email: string; state: string; error: string };
type Rule = {
  id: string;
  mailbox_id: string;
  employer_origin: string;
  employer_path: string;
  sender_domains: string[];
  link_origins: string[];
};
type Challenge = {
  id: string;
  run_id: string;
  kind: string;
  state: string;
  reason: string;
  created_at: string;
};
type Status = {
  configured: boolean;
  redirect_uri: string;
  mailboxes: Mailbox[];
  rules: Rule[];
  challenges: Challenge[];
};
type Request = (path: string, method?: string, body?: unknown) => Promise<any>;
type Action = (fn: () => Promise<unknown>, success?: string) => Promise<void>;
const words = (value: string) => value.replaceAll("_", " ");
const split = (value: string) =>
  value
    .split(/[\s,]+/)
    .map((x) => x.trim())
    .filter(Boolean);

export function MailSettings({
  request,
  act,
}: {
  request: Request;
  act: Action;
}) {
  const [data, setData] = useState<Status | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [mailbox, setMailbox] = useState("");
  const [employer, setEmployer] = useState("");
  const [senders, setSenders] = useState("");
  const [origins, setOrigins] = useState("");
  const [busy, setBusy] = useState(false);
  const reload = async () => {
    const result = await request("/mail");
    setData(result);
    setError("");
  };
  useEffect(() => {
    let alive = true;
    const load = () =>
      request("/mail")
        .then((result) => {
          if (alive) {
            setData(result);
            setError("");
          }
        })
        .catch((e) => {
          if (alive) setError(e.message);
        });
    load();
    const timer = setInterval(load, 3000);
    const url = new URL(location.href);
    if (url.searchParams.has("gmail")) {
      setNotice(
        url.searchParams.get("gmail") === "connected"
          ? "Gmail connected. Add an employer rule to enable verification."
          : "Gmail connection failed. Check the OAuth settings and try again.",
      );
      url.searchParams.delete("gmail");
      history.replaceState(null, "", url);
    }
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);
  const change = async (fn: () => Promise<unknown>, success?: string) => {
    setBusy(true);
    try {
      await act(async () => {
        await fn();
        await reload();
      }, success);
    } finally {
      setBusy(false);
    }
  };
  const selectedMailbox = data?.mailboxes.some((m) => m.id === mailbox)
    ? mailbox
    : data?.mailboxes[0]?.id || "";
  return (
    <section className="panel form-panel subsection mail-settings">
      <div className="section-top">
        <div>
          <h2>Email verification</h2>
          <p className="muted">
            Connect Gmail to handle email codes and verification links during
            applications.
          </p>
        </div>
        <Mail size={21} />
      </div>
      {error && <p role="alert">{error}</p>}
      {notice && (
        <p role="status" className="note">
          {notice}
        </p>
      )}
      {!data ? (
        <p className="muted">Loading email connections…</p>
      ) : (
        <>
          <div className="mail-connect">
            <p>
              Read-only access. Verification checks the recipient, sender,
              message time, and allowed link destinations. Codes and links stay
              private.
            </p>
            <button
              className="button primary"
              disabled={busy || !data.configured}
              onClick={() =>
                change(async () => {
                  const result = await request("/mail/connect", "POST");
                  window.location.assign(result.authorization_url);
                })
              }
            >
              <Plus size={16} /> Connect Gmail
            </button>
          </div>
          {!data.configured && (
            <p className="note small">
              Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in the server’s .env
              file, then restart. Enable the Gmail API in your Google Cloud
              project.
            </p>
          )}
          <p className="muted small">
            Google OAuth redirect URI:{" "}
            <code className="mail-uri">{data.redirect_uri}</code>
          </p>
          {data.mailboxes.length === 0 && (
            <p className="muted">No mailbox connected yet.</p>
          )}
          {data.mailboxes.map((box) => (
            <div className="mail-box" key={box.id}>
              <div>
                <strong>{box.email}</strong>
                <span
                  className={
                    "badge " + (box.state === "connected" ? "good" : "warn")
                  }
                >
                  {words(box.state)}
                </span>
                {box.error && <p className="muted small">{box.error}</p>}
              </div>
              <div className="mail-actions">
                <button
                  className="button"
                  disabled={busy}
                  onClick={() =>
                    change(
                      () => request(`/mail/${box.id}/check`, "POST"),
                      "Gmail connection checked",
                    )
                  }
                >
                  <RefreshCw size={15} /> Check
                </button>
                <button
                  className="button"
                  disabled={busy}
                  onClick={() =>
                    change(async () => {
                      const result = await request(`/mail/${box.id}`, "DELETE");
                      setNotice(
                        result.revoked
                          ? "Mailbox disconnected and Google access revoked."
                          : "Mailbox disconnected locally. Google revocation failed; remove access in your Google account settings.",
                      );
                    })
                  }
                >
                  <Unplug size={15} /> Disconnect
                </button>
              </div>
            </div>
          ))}
          <div className="mail-rules">
            <h3>Employer rules</h3>
            <p className="muted small">
              Create a rule once per employer. Use the employer’s URL path on
              shared ATS domains, exact email sender domains, and any additional
              login or verification origins.
            </p>
            {data.mailboxes.length > 0 && (
              <form
                className="mail-rule-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  change(async () => {
                    await request("/mail-rules", "POST", {
                      mailbox_id: selectedMailbox,
                      employer_origin: employer,
                      sender_domains: split(senders),
                      link_origins: split(origins),
                    });
                    setEmployer("");
                    setSenders("");
                    setOrigins("");
                  }, "Employer verification rule saved");
                }}
              >
                <label className="field">
                  <span>Mailbox</span>
                  <select
                    value={selectedMailbox}
                    onChange={(e) => setMailbox(e.target.value)}
                  >
                    {data.mailboxes.map((box) => (
                      <option key={box.id} value={box.id}>
                        {box.email}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>Employer application URL or path prefix</span>
                  <input
                    type="url"
                    required
                    value={employer}
                    onChange={(e) => setEmployer(e.target.value)}
                    placeholder="https://careers.example.com/company"
                  />
                </label>
                <label className="field">
                  <span>Trusted sender domains</span>
                  <input
                    required
                    value={senders}
                    onChange={(e) => setSenders(e.target.value)}
                    placeholder="notifications.example.com"
                  />
                  <small>
                    Exact domains, separated by commas. No wildcards.
                  </small>
                </label>
                <label className="field">
                  <span>Additional verification origins</span>
                  <input
                    value={origins}
                    onChange={(e) => setOrigins(e.target.value)}
                    placeholder="https://login.example.com"
                  />
                  <small>
                    Optional. The employer origin is already allowed.
                  </small>
                </label>
                <button className="button" disabled={busy}>
                  <Plus size={16} /> Save employer rule
                </button>
              </form>
            )}
            {data.rules.map((rule) => (
              <div className="mail-box" key={rule.id}>
                <div>
                  <strong>
                    {rule.employer_origin}
                    {rule.employer_path === "/" ? "" : rule.employer_path}
                  </strong>
                  <p className="muted small">
                    From: {rule.sender_domains.join(", ")}
                  </p>
                  <p className="muted small">
                    Links: {rule.link_origins.join(", ")}
                  </p>
                </div>
                <button
                  aria-label={`Remove rule for ${rule.employer_origin}${rule.employer_path}`}
                  className="icon-button"
                  disabled={busy}
                  onClick={() =>
                    change(
                      () => request(`/mail-rules/${rule.id}`, "DELETE"),
                      "Employer rule removed",
                    )
                  }
                >
                  <X size={17} />
                </button>
              </div>
            ))}
            <p className="muted small">
              Dedicated email-verification steps are supported. Password
              creation, SMS codes, and passkeys still require an employer
              session. A missing or ambiguous email stops that run for review.
            </p>
          </div>
          <h3>Verification history</h3>
          {data.challenges.length === 0 ? (
            <p className="muted small">
              Email challenges will appear here when an application requests
              verification.
            </p>
          ) : (
            <div className="mail-history">
              {data.challenges.map((c) => (
                <div className="mail-box" key={c.id}>
                  <div>
                    <span
                      className={
                        "badge " + (c.state === "verified" ? "good" : "")
                      }
                    >
                      {words(c.state)}
                    </span>
                    <strong>
                      {c.kind === "auto" ? "Email" : words(c.kind)} · Run{" "}
                      {c.run_id.slice(0, 8)}
                    </strong>
                    <p className="muted small">
                      {c.reason || "Waiting for verification"} ·{" "}
                      {new Date(c.created_at).toLocaleString()}
                    </p>
                  </div>
                  {["pending", "matched", "consumed"].includes(c.state) && (
                    <button
                      className="button"
                      disabled={busy}
                      onClick={() =>
                        change(
                          () =>
                            request(`/mail-challenges/${c.id}/cancel`, "POST"),
                          "Verification cancelled",
                        )
                      }
                    >
                      Cancel
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}
