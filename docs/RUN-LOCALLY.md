# ApplyPilot: everyday use

## Start

1. Open Docker Desktop and leave it running.
2. Open Terminal and paste:

   ```sh
   cd /Users/premalparagbhaishah/Desktop/applypilot/ApplyPilot
   python -m jobpilot.cli browser
   docker compose up -d
   ```

3. Leave the new Chrome window open, then open http://127.0.0.1:8080.
4. If asked for the access token, run `docker compose exec app jobpilot token` and paste its output into the app.

## Apply

Your profile is set to autonomous applications. Paste a job link into **Applications → Import job**. The app selects an uploaded résumé, checks the match, and queues suitable jobs automatically. Sources can discover and queue jobs on a schedule.

Five workers can run at once. Applications from the same employer stay in order. Email checks from a shared sender are briefly serialized so a code cannot go into the wrong application.

If an answer is missing, the app asks you in Telegram, saves your reply, and continues automatically. Other applications can keep running while it waits. An uncertain submission is not retried automatically, to avoid duplicates. An explicit spam rejection pauses that employer for six hours.

**Confirmed** means the app captured a website receipt or matched an authenticated company acknowledgement email. Open the run for its evidence. An email verification code by itself does not count as an application receipt.

## Your knowledge base

- **Layer 1: Confirmed facts.** Contact details, your explicit answers, and saved personal information.
- **Layer 2: Experience, stories and narratives.** Your résumé and examples. AI uses these with the job description to write relevant answers.
- Save your notes, then use **Organize saved facts with AI** to extract useful details. Extracted facts must match text in your notes; your existing facts take priority.
- Application source is LinkedIn. Terms and conditions are accepted according to the switches in your knowledge base.

## Gmail

Gmail can be connected using an app password:

1. Sign into the Gmail account you use for applications.
2. Open https://myaccount.google.com/security and enable **2-Step Verification**.
3. Open https://myaccount.google.com/apppasswords. Create a password named **ApplyPilot**.
4. In `.env`, enter:

   ```dotenv
   GMAIL_ADDRESS=your-address@gmail.com
   GMAIL_APP_PASSWORD=your-generated-app-password
   ```

5. Run `docker compose up -d --force-recreate app`.
6. In ApplyPilot, open **Settings → Email verification → Check connection**.

Use Google's generated app password, not your normal Gmail password. Keep it in `.env`. The app reads messages without marking them read and never sends email. Known Greenhouse and Workday verification rules are created automatically.

If Google does not offer app passwords, the app also supports Google sign-in. See [email-verification.md](email-verification.md) for the one-time Google Cloud setup.

## Telegram

Open your configured bot in Telegram and press **Start**.

- Paste a job link to add an application.
- Say **status**, **pause applications**, or **resume applications**.
- Reply normally to a question. Your answer is saved in the knowledge base and reused.
- If it asks you to finish a security check in Chrome, complete the check and reply **done** to that Telegram message. The app continues from the open page.

The bot also answers questions about recent applications and sends a result after each run. It accepts messages only from the Telegram account saved in `.env`.

## Workday, Oracle, iCIMS and other multi-page portals

These portals use dedicated drivers (`ENGINE=adapters`, the default). Set `ENGINE=guided` in `.env` to use the previous guided flow for every site. Before enabling automatic submission for a new portal, try a dry run that never submits and read its step trace:

```sh
docker compose exec app jobpilot probe "JOB_URL" --company "Employer"
docker compose exec app jobpilot trace RUN_ID
```

See [integration.md](integration.md) for what changed in this version and how runs are confirmed.

## Workday sign-in

Workday may ask you to sign in again when its session expires. The app uses its saved account details and connected Gmail for supported account recovery. If it asks for help in Telegram, finish signing in in the open Chrome tab, then reply **done** to that message. Do not paste your Workday password into chat or `.env`. Employer outages and security challenges can still prevent an application.

## Accounts, security checks, and location

- New employer accounts use your application email and one shared password, stored encrypted. Existing saved accounts keep their passwords until a reset is confirmed. The AI never receives passwords.
- The application browser denies requests for your live location, including in new tabs.
- CapSolver handles supported reCAPTCHA and Turnstile token challenges automatically.
- For hCaptcha image puzzles, the optional `TWOCAPTCHA_API_KEY` enables a 2Captcha image-coordinate fallback. Only the isolated puzzle image is sent. Its success on an employer site must still be verified; it is not universal CAPTCHA support.
- Solver calls stop after two attempts per challenge, four paid image rounds per application and 90 seconds per task by default. Configure `CAPTCHA_MAX_ATTEMPTS`, `CAPTCHA_MAX_ROUNDS` and `CAPTCHA_TIMEOUT` in `.env` if needed. Restart the app after changing `.env`.
- Unfamiliar application sites use the same form and account detector. A new email sender can be learned automatically when a fresh, authenticated verification message links directly to that employer. Ambiguous messages still require help.

## Stop or update

Use **Settings → Pause workers** to stop new work. To stop the app itself, run `docker compose stop`. Your saved information stays in Docker's data volumes.

After a code update, run `docker compose up --build -d`. Keep your Mac awake, Docker running, and the network connected while applications run.
