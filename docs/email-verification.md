# Gmail connections and email verification

Implemented in the app; validation updated 2026-10-02 UTC. This uses the application's own Google OAuth connection. It does not depend on a ChatGPT Gmail plugin or inherit its authorization.

## Connect a mailbox

1. In your Google Cloud project, enable **Gmail API**. Configure the OAuth consent screen and add your account as a test user if the project is in Testing.
2. Create an OAuth client of type **Web application**. Add the exact redirect URI displayed in **Settings → Email verification**. The default native-install URI is `http://127.0.0.1:8080/oauth/gmail/callback`. For a hosted instance use its HTTPS `BASE_URL` and set `SECURE_COOKIES=true`. Access the dashboard through that same base URL.
3. Add `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` to the server's private `.env`. Restart the application. These are Google OAuth credentials, separate from the MiMo, Telegram, and CapSolver keys.
4. Open **Settings → Email verification → Connect Gmail**. Select the mailbox and grant read-only access. The callback returns to Settings with the connection result.
5. Use **Check** to confirm the API connection. **Disconnect** removes the local credentials and employer rules, cancels active challenges, and attempts Google token revocation. If revocation fails, the dashboard says so.

The requested scope is `gmail.readonly`: it grants broad read access to that mailbox, although the application only retrieves candidate messages for active verification challenges. It does not grant send, delete, or mailbox modification privileges. Google classifies this scope as restricted; applicable verification requirements depend on how the OAuth application is distributed. External OAuth projects in Testing generally receive seven-day refresh tokens for Gmail access. An expired/revoked grant becomes `reconnect required`; connect again after addressing the OAuth configuration. [1][2][3]

## Employer rules

Add a rule once for each employer flow:

| Setting | Meaning |
| --- | --- |
| Mailbox | The connected inbox receiving the applicant's verification email |
| Employer application URL or path prefix | Exact origin plus optional tenant path; use an employer-specific prefix on shared ATS domains |
| Trusted sender domains | Exact domains from the employer's legitimate verification emails; transactional senders may differ from the employer's website |
| Additional verification origins | Exact HTTPS origins needed for login and verification redirects; the employer origin is automatically included |

For example, `https://careers.example.com/company-a` matches that path and its descendants, but not `/company-ab` or `/company-b`. These example domains are placeholders, not built-in ATS mappings. Rules come from the applicant's setup; page text, email links, and model output cannot expand them. Saving the same employer origin/path updates that rule, unless it has an active challenge.

The recipient is the email in the run's frozen applicant profile. A connected mailbox may receive mail through an alias, but its message must name that exact profile address in `To` or `Delivered-To`. The app checks Google's receiving authentication result for aligned DMARC, the exact sender domain, recipient, and bounded delivery window. Shared sender groups in the same mailbox are serialized across runs to reduce ambiguity. Missing rules, unrecognized message templates, failed authentication, or multiple matching messages stop the run for review.

Origin restrictions do not prove an employer tenant's identity within a shared origin. Sender configuration and the pending challenge window are also required. Initial-page challenges use the current run's start time; explicit Send Code/Link actions establish the window before clicking. Delayed messages from an earlier request and employer-specific challenge IDs still require adapter work where the generic envelope checks cannot distinguish them. This is not a guarantee that every ATS message can be correlated.

## What runs automatically

- OAuth renewal, encrypted token storage, connection health checks, and revocation attempts.
- Gmail candidate-message polling only while a challenge is pending. Default cadence: 3 seconds; default deadline: 60 seconds.
- Single email-code inputs and 4–10 separate single-character inputs on dedicated authentication pages.
- Clearly labeled code/passcode messages and verification links in plain text or HTML MIME parts, with bounded parsing.
- One-use message consumption and encrypted temporary challenge payloads, cleared after claim/cancellation/expiry.
- One-time links opened in the owning browser context, with exact-origin checks and Chromium Fetch interception of redirect hops. No preview request consumes a link before the browser opens it.
- Verification completion checks before normal form filling continues. Email verification is never counted as an application-submission receipt.
- Redacted verification events and history in Settings. The answer model receives no mailbox content or code. Browser exceptions from verification are replaced with non-secret diagnostics.

Email wait time is included in the existing application wall-clock deadline. This implementation holds the browser slot while waiting; it does **not** implement a separate parked-context pool or resumable cross-process challenge continuation. Applications sharing ambiguous sender domains may require requeueing after the active challenge finishes. There are no automatic resends or password resets.

## Boundaries

Account/password creation, stored employer passwords, SMS, passkeys, device approval, and complex mixed application/authentication widgets are not implemented by this feature. Use an employer session for those flows. Link tracking origins must be explicitly configured. Employer-specific templates, login buttons, and post-verification transitions may need adapters. OTP verification after final submission is also not a supported generic flow: it must not bypass the submission reservation/receipt state machine.

Gmail receipt reconciliation, Pub/Sub notifications, IMAP, and Outlook are not implemented. There is no unrestricted email-search tool exposed to the browser agent or MCP clients. This feature supports dedicated email verification across the generic browser path; it is not certification of Greenhouse, Workday, Oracle, iCIMS, Ashby, or SmartRecruiters employer variants.

Tokens and temporary matched payloads use authenticated Fernet encryption. By default the key is generated once at `DATA_DIR/mail-key` with owner-only permissions. Back up that key with appropriate access controls; losing it makes the existing connections unreadable. For a separately managed deployment secret, set `MAIL_ENCRYPTION_KEY` to a valid Fernet key before creating connections. Changing it does not migrate old ciphertext. Imported employer session files retain the existing private-file storage format; this feature does not encrypt them.

## Validation

Tests use synthetic Gmail responses, actual OAuth callback handlers, real Chromium, and owned browser fixtures. They cover OAuth browser binding/replay, refresh, encrypted storage, exact rule scope, stale/wrong/ambiguous messages, one-use consumption, cancellation/expiry, invalid grants, code and link verification, redirect blocking, and continued form fill/upload with the final-submit guard intact. No real Google consent grant, inbox, or employer authentication flow has been tested without your credentials.

References:

1. [Google OAuth web-server flow](https://developers.google.com/identity/protocols/oauth2/web-server)
2. [Gmail API scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)
3. [Google OAuth token lifecycle](https://developers.google.com/identity/protocols/oauth2#expiration)
4. [Gmail message listing](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list)
